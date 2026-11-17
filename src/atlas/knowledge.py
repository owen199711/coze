from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .db import Database
from .models import KnowledgeItem, SourceRef, new_id, utcnow

VALID_KINDS = {
    "preference",
    "fact",
    "decision",
    "lesson",
    "doc",
    "command",
    "contract",
    "assumption",
    "glossary",
}
VALID_SCOPES = {"user", "project", "org"}
VALID_STATUSES = {"candidate", "active", "stale", "retired", "quarantined"}
VALID_SENSITIVITIES = {"public", "internal", "secret"}
VALIDATION_PASSED = {"passed", "pass", "ok", "verified", "success"}


def _validation_passed(validation: dict[str, Any] | None) -> bool:
    if not isinstance(validation, dict):
        return False
    result = str(validation.get("result") or validation.get("status") or "").lower()
    return result in VALIDATION_PASSED


@dataclass
class KnowledgeHit:
    item: KnowledgeItem
    score: float
    snippet: str


class KnowledgeService:
    """One store for facts, preferences, decisions, lessons, docs and commands."""

    def __init__(
        self,
        db: Database,
        project_id: str,
        tenant_id: str = "local",
    ):
        self.db = db
        self.project_id = project_id
        self.tenant_id = tenant_id
        self._fts = False
        self._init_fts()

    # ---------------------------------------------------------------- schema
    def _init_fts(self) -> None:
        try:
            self.db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
                USING fts5(item_id UNINDEXED, title, body, tags)
                """
            )
            self.db.commit()
            self._fts = True
        except Exception:
            try:
                self.db.connect().rollback()
            except Exception:
                pass
            self._fts = False

    # ---------------------------------------------------------------- write
    def write(
        self,
        item: KnowledgeItem,
        actor: str = "agent",
        allow_secret: bool = False,
    ) -> str:
        if item.kind not in VALID_KINDS:
            raise ValueError(f"invalid kind: {item.kind}")
        if item.scope not in VALID_SCOPES:
            raise ValueError(f"invalid scope: {item.scope}")
        if item.status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {item.status}")
        if item.sensitivity not in VALID_SENSITIVITIES:
            raise ValueError(f"invalid sensitivity: {item.sensitivity}")
        if item.sensitivity == "secret" and not allow_secret:
            raise ValueError("secret knowledge is not stored by default")
        if not item.title.strip() or not item.body.strip():
            raise ValueError("title and body are required")
        if (
            item.kind == "lesson"
            and item.status == "active"
            and not _validation_passed(item.validation)
        ):
            raise ValueError(
                "lesson requires a passed validation (replay/test/human) before status=active"
            )

        project_id = item.project_id if item.project_id is not None else self.project_id
        if item.scope != "project":
            project_id = ""

        dedupe_key = hashlib.sha256(
            f"{item.kind}|{item.title.strip().lower()}|{item.body.strip()}".encode("utf-8")
        ).hexdigest()[:32]

        existing = self.db.fetchone(
            """
            SELECT * FROM knowledge_items
            WHERE tenant_id = ? AND project_id = ? AND scope = ? AND kind = ? AND dedupe_key = ?
            """,
            (self.tenant_id, project_id, item.scope, item.kind, dedupe_key),
        )
        if existing:
            existing_item = self._row_to_item(existing)
            status = existing_item.status
            if _validation_passed(item.validation):
                status = "active"
            elif item.status == "active" and status != "quarantined":
                status = "active"
            tags = sorted(set(existing_item.tags) | set(item.tags))
            validation = dict(existing_item.validation or {})
            validation.update(item.validation or {})
            source = item.source if item.source.ref else existing_item.source
            severity_rank = {"low": 0, "medium": 1, "high": 2}
            severity = max(
                (existing_item.severity, item.severity),
                key=lambda value: severity_rank.get(value, 1),
            )
            self.db.execute(
                """
                UPDATE knowledge_items
                SET status = ?, tags_json = ?, source_json = ?, confidence = MAX(confidence, ?),
                    severity = ?, validation_json = ?, expires_at = COALESCE(?, expires_at),
                    updated_at = ?, freshness_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(source.to_dict(), ensure_ascii=False),
                    item.confidence,
                    severity,
                    json.dumps(validation, ensure_ascii=False) if validation else None,
                    item.expires_at,
                    utcnow(),
                    utcnow(),
                    existing_item.id,
                ),
            )
            if self._fts:
                try:
                    self.db.execute(
                        "UPDATE knowledge_fts SET tags = ? WHERE item_id = ?",
                        (" ".join(tags), existing_item.id),
                    )
                except Exception:
                    pass
            self.db.commit()
            return existing_item.id

        now = utcnow()
        item.id = item.id or new_id("kn")
        item.created_at = item.created_at or now
        item.updated_at = now
        item.freshness_at = item.freshness_at or now
        self.db.execute(
            """
            INSERT INTO knowledge_items (
              id, tenant_id, project_id, owner_id, scope, kind, status, title, body,
              tags_json, source_json, confidence, severity, validation_json,
              freshness_at, expires_at, supersedes_json, sensitivity, dedupe_key,
              use_count, last_used_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
            """,
            (
                item.id,
                self.tenant_id,
                project_id,
                item.owner_id,
                item.scope,
                item.kind,
                item.status,
                item.title,
                item.body,
                json.dumps(item.tags, ensure_ascii=False),
                json.dumps(item.source.to_dict(), ensure_ascii=False),
                item.confidence,
                item.severity,
                json.dumps(item.validation, ensure_ascii=False) if item.validation else None,
                item.freshness_at,
                item.expires_at,
                json.dumps([], ensure_ascii=False),
                item.sensitivity,
                dedupe_key,
                item.created_at,
                item.updated_at,
            ),
        )

        for ordinal, chunk in enumerate(self._chunks(item.body)):
            chunk_id = new_id("ch")
            self.db.execute(
                """
                INSERT INTO knowledge_chunks
                (id, item_id, project_id, ordinal, content, symbol, start_line, end_line,
                 token_count, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    chunk_id,
                    item.id,
                    project_id,
                    ordinal,
                    chunk,
                    max(1, len(chunk) // 4),
                    now,
                ),
            )

        if self._fts:
            try:
                self.db.execute(
                    "INSERT INTO knowledge_fts(item_id, title, body, tags) VALUES (?, ?, ?, ?)",
                    (item.id, item.title, item.body, " ".join(item.tags)),
                )
            except Exception:
                pass
        self.db.commit()
        return item.id

    def _chunks(self, body: str, size: int = 900) -> list[str]:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        if not paragraphs:
            return [body[:size]]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if len(current) + len(paragraph) + 2 <= size:
                current = f"{current}\n\n{paragraph}".strip()
            else:
                if current:
                    chunks.append(current)
                while len(paragraph) > size:
                    chunks.append(paragraph[:size])
                    paragraph = paragraph[size:]
                current = paragraph
        if current:
            chunks.append(current)
        return chunks or [body[:size]]

    # ----------------------------------------------------------------- read
    def get(self, item_id: str) -> KnowledgeItem | None:
        row = self.db.fetchone(
            "SELECT * FROM knowledge_items WHERE id = ? AND tenant_id = ? "
            "AND (scope != 'project' OR project_id = ?)",
            (item_id, self.tenant_id, self.project_id),
        )
        return self._row_to_item(row) if row else None

    def find_by_source(self, kind: str, ref: str) -> KnowledgeItem | None:
        try:
            row = self.db.fetchone(
                "SELECT * FROM knowledge_items WHERE tenant_id = ? AND kind = ? "
                "AND (scope != 'project' OR project_id = ?) "
                "AND json_extract(source_json, '$.ref') = ? LIMIT 1",
                (self.tenant_id, kind, self.project_id, ref),
            )
        except Exception:
            row = None
        if row:
            return self._row_to_item(row)
        # Older SQLite builds may lack JSON1; fall back to a literal scan.
        row = self.db.fetchone(
            "SELECT * FROM knowledge_items WHERE tenant_id = ? AND kind = ? "
            "AND (scope != 'project' OR project_id = ?) AND source_json LIKE ? LIMIT 1",
            (self.tenant_id, kind, self.project_id, f'%"{ref}"%'),
        )
        return self._row_to_item(row) if row else None

    def search(
        self,
        query: str,
        scope: str | None = None,
        kinds: Iterable[str] | None = None,
        limit: int = 8,
        budget_tokens: int = 2000,
    ) -> list[KnowledgeHit]:
        query = (query or "").strip()
        if not query:
            return []
        kinds_list = [k for k in (kinds or []) if k in VALID_KINDS] or None
        rows: list[tuple[Any, float]] = []

        if self._fts:
            fts_query = self._fts_query(query)
            if fts_query:
                try:
                    sql = """
                        SELECT ki.*, bm25(knowledge_fts) AS rank
                        FROM knowledge_fts
                        JOIN knowledge_items ki ON ki.id = knowledge_fts.item_id
                        WHERE knowledge_fts MATCH ? AND ki.tenant_id = ? AND ki.status = 'active'
                    """
                    params: list[object] = [fts_query, self.tenant_id]
                    sql, params = self._apply_filters(sql, params, scope, kinds_list)
                    sql += " ORDER BY rank LIMIT ?"
                    params.append(max(limit * 3, limit))
                    for row in self.db.fetchall(sql, params):
                        rows.append((row, float(row["rank"] or 0.0)))
                except Exception:
                    rows = []

        if not rows:
            sql = """
                SELECT * FROM knowledge_items
                WHERE tenant_id = ? AND status = 'active'
                  AND (title LIKE ? OR body LIKE ? OR tags_json LIKE ?)
            """
            like = f"%{query}%"
            params = [self.tenant_id, like, like, like]
            sql, params = self._apply_filters(sql, params, scope, kinds_list)
            sql += " ORDER BY confidence DESC, updated_at DESC LIMIT ?"
            params.append(max(limit * 3, limit))
            for row in self.db.fetchall(sql, params):
                rows.append((row, 0.0))

        hits: list[KnowledgeHit] = []
        used = 0
        seen: set[str] = set()
        for row, rank in rows:
            item_id = str(row["id"])
            if item_id in seen:
                continue
            seen.add(item_id)
            item = self._row_to_item(row)
            cost = max(1, len(item.body) // 4)
            if hits and used + cost > budget_tokens:
                continue
            used += cost
            hits.append(
                KnowledgeHit(
                    item=item,
                    score=(-rank) if rank else float(item.confidence),
                    snippet=self._snippet(item.body, query),
                )
            )
            if len(hits) >= limit:
                break

        if hits:
            ids = [h.item.id for h in hits]
            placeholders = ",".join("?" for _ in ids)
            self.db.execute(
                f"UPDATE knowledge_items SET use_count = use_count + 1, last_used_at = ? "
                f"WHERE id IN ({placeholders})",
                [utcnow(), *ids],
            )
            self.db.commit()
        return hits

    def _fts_query(self, query: str) -> str:
        terms = re.findall(r"[\w\u4e00-\u9fff]+", query)
        if not terms:
            return ""
        return " OR ".join(f'"{term}"' for term in terms[:8])

    def _apply_filters(
        self,
        sql: str,
        params: list[object],
        scope: str | None,
        kinds: list[str] | None,
    ) -> tuple[str, list[object]]:
        prefix = "ki." if "knowledge_fts" in sql else ""
        # Project isolation: project-scoped rows are visible only to their project.
        sql += f" AND ({prefix}scope != 'project' OR {prefix}project_id = ?)"
        params.append(self.project_id)
        if scope:
            sql += f" AND {prefix}scope = ?"
            params.append(scope)
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            sql += f" AND {prefix}kind IN ({placeholders})"
            params.extend(kinds)
        return sql, params

    def _snippet(self, body: str, query: str, width: int = 240) -> str:
        terms = [t for t in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(t) > 1]
        lower = body.lower()
        for term in terms:
            idx = lower.find(term.lower())
            if idx >= 0:
                start = max(0, idx - width // 3)
                return body[start : start + width].replace("\n", " ").strip()
        return body[:width].replace("\n", " ").strip()

    # --------------------------------------------------------------- update
    def feedback(self, item_id: str, outcome: str) -> bool:
        row = self.db.fetchone(
            "SELECT confidence FROM knowledge_items WHERE id = ? AND tenant_id = ? "
            "AND (scope != 'project' OR project_id = ?)",
            (item_id, self.tenant_id, self.project_id),
        )
        if not row:
            return False
        confidence = float(row["confidence"] or 0.8)
        if outcome in {"effective", "used", "good"}:
            confidence = min(1.0, confidence + 0.02)
            status = "active"
        elif outcome in {"ineffective", "bad", "wrong"}:
            confidence = max(0.0, confidence - 0.15)
            status = "quarantined" if confidence < 0.3 else "active"
        else:
            raise ValueError("outcome must be effective/used/good or ineffective/bad/wrong")
        self.db.execute(
            "UPDATE knowledge_items SET confidence = ?, status = ?, updated_at = ?, last_used_at = ? "
            "WHERE id = ? AND tenant_id = ? AND (scope != 'project' OR project_id = ?)",
            (
                confidence,
                status,
                utcnow(),
                utcnow(),
                item_id,
                self.tenant_id,
                self.project_id,
            ),
        )
        self.db.commit()
        return True

    def promote(self, item_id: str, validation: dict[str, Any] | None = None) -> bool:
        if not _validation_passed(validation):
            return False
        row = self.db.fetchone(
            "SELECT id FROM knowledge_items WHERE id = ? AND tenant_id = ? "
            "AND (scope != 'project' OR project_id = ?)",
            (item_id, self.tenant_id, self.project_id),
        )
        if not row:
            return False
        self.db.execute(
            "UPDATE knowledge_items SET status = 'active', validation_json = ?, "
            "updated_at = ? WHERE id = ? AND tenant_id = ? "
            "AND (scope != 'project' OR project_id = ?)",
            (
                json.dumps(validation, ensure_ascii=False),
                utcnow(),
                item_id,
                self.tenant_id,
                self.project_id,
            ),
        )
        self.db.commit()
        return True

    def forget(self, item_id: str) -> bool:
        row = self.db.fetchone(
            "SELECT id FROM knowledge_items WHERE id = ? AND tenant_id = ? "
            "AND (scope != 'project' OR project_id = ?)",
            (item_id, self.tenant_id, self.project_id),
        )
        if not row:
            return False
        if self._fts:
            try:
                self.db.execute("DELETE FROM knowledge_fts WHERE item_id = ?", (item_id,))
            except Exception:
                pass
        self.db.execute("DELETE FROM knowledge_chunks WHERE item_id = ?", (item_id,))
        cur = self.db.execute(
            "DELETE FROM knowledge_items WHERE id = ? AND tenant_id = ? "
            "AND (scope != 'project' OR project_id = ?)",
            (item_id, self.tenant_id, self.project_id),
        )
        self.db.commit()
        return cur.rowcount > 0

    def list(
        self,
        scope: str | None = None,
        kind: str | None = None,
        status: str | None = "active",
        limit: int = 50,
    ) -> list[KnowledgeItem]:
        sql = "SELECT * FROM knowledge_items WHERE tenant_id = ?"
        params: list[object] = [self.tenant_id]
        if self.project_id:
            sql += " AND (project_id = ? OR scope != 'project')"
            params.append(self.project_id)
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_item(row) for row in self.db.fetchall(sql, params)]

    def consolidate(
        self,
        stale_days: int = 90,
        candidate_days: int = 30,
    ) -> dict[str, int]:
        """Retire expired knowledge and age out stale docs/candidates."""
        now = datetime.now(timezone.utc)
        cutoff_stale = (now - timedelta(days=stale_days)).isoformat(timespec="microseconds")
        cutoff_candidate = (now - timedelta(days=candidate_days)).isoformat(timespec="microseconds")
        now_iso = now.isoformat(timespec="microseconds")

        expired = self.db.execute(
            "UPDATE knowledge_items SET status = 'retired', updated_at = ? "
            "WHERE tenant_id = ? AND (scope != 'project' OR project_id = ?) "
            "AND status = 'active' AND expires_at IS NOT NULL AND expires_at < ?",
            (now_iso, self.tenant_id, self.project_id, now_iso),
        ).rowcount
        stale_docs = self.db.execute(
            "UPDATE knowledge_items SET status = 'stale', updated_at = ? "
            "WHERE tenant_id = ? AND (scope != 'project' OR project_id = ?) "
            "AND status = 'active' AND kind = 'doc' AND freshness_at < ?",
            (now_iso, self.tenant_id, self.project_id, cutoff_stale),
        ).rowcount
        old_candidates = self.db.execute(
            "UPDATE knowledge_items SET status = 'retired', updated_at = ? "
            "WHERE tenant_id = ? AND (scope != 'project' OR project_id = ?) "
            "AND status = 'candidate' AND created_at < ?",
            (now_iso, self.tenant_id, self.project_id, cutoff_candidate),
        ).rowcount
        self.db.commit()
        return {
            "expired": int(expired or 0),
            "stale_docs": int(stale_docs or 0),
            "old_candidates": int(old_candidates or 0),
        }

    def stats(self) -> dict[str, dict[str, int]]:
        rows = self.db.fetchall(
            "SELECT kind, status, COUNT(*) AS n FROM knowledge_items "
            "WHERE tenant_id = ? AND (scope != 'project' OR project_id = ?) "
            "GROUP BY kind, status",
            (self.tenant_id, self.project_id),
        )
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            result.setdefault(row["kind"], {})[row["status"]] = int(row["n"])
        return result

    # --------------------------------------------------------------- helper
    def _row_to_item(self, row: object) -> KnowledgeItem:
        import sqlite3

        assert isinstance(row, sqlite3.Row)
        source_data = json.loads(row["source_json"] or "{}")
        tags = json.loads(row["tags_json"] or "[]")
        validation = json.loads(row["validation_json"]) if row["validation_json"] else None
        return KnowledgeItem(
            id=row["id"],
            tenant_id=row["tenant_id"],
            project_id=row["project_id"] or None,
            owner_id=row["owner_id"],
            scope=row["scope"],
            kind=row["kind"],
            status=row["status"],
            title=row["title"],
            body=row["body"],
            tags=tags,
            source=SourceRef(
                kind=source_data.get("kind", "session"),
                ref=source_data.get("ref", ""),
                quote=source_data.get("quote"),
            ),
            confidence=float(row["confidence"] or 0.8),
            severity=row["severity"] or "medium",
            validation=validation,
            freshness_at=row["freshness_at"] or row["updated_at"],
            expires_at=row["expires_at"],
            sensitivity=row["sensitivity"] or "internal",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            use_count=int(row["use_count"] or 0),
            last_used_at=row["last_used_at"],
        )
