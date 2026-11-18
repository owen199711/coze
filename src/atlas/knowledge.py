from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
VALID_TRUST = {"trusted", "derived", "untrusted"}

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}"
    ),
)
VALIDATION_PASSED = {"passed", "pass", "ok", "verified", "success"}

# CJK 字符（含中日韩统一表意文字、假名、谚文、全角标点）在多数分词器里
# 约等于 1 字 1 token，而 ASCII 约 4 字符 1 token。旧实现统一用 len//4，
# 会让中文记忆的预算成本被低估约 4 倍。
_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]"
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate that does not undercount CJK text.

    CJK characters count as one token each; the remaining characters count
    as one token per four. Always returns at least 1 so that empty or
    whitespace-only content still costs something against a budget.
    """
    if not text:
        return 1
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return max(1, cjk + other // 4)


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
    origin: str = "fts"
    chunk_id: str | None = None


DEFAULT_KIND_BUDGETS = {
    "preference": 300,
    "fact": 800,
    "decision": 800,
    "lesson": 800,
    "doc": 3000,
    "command": 300,
    "contract": 500,
    "assumption": 300,
    "glossary": 300,
}

KIND_HALF_LIFE_DAYS = {
    "preference": 3650,
    "fact": 365,
    "decision": 3650,
    "lesson": 180,
    "doc": 180,
    "command": 365,
    "contract": 365,
    "assumption": 30,
    "glossary": 3650,
}

SEVERITY_WEIGHT = {"low": 0.8, "medium": 1.0, "high": 1.2}

# Long-lived but not permanent: an item nobody recalled within this window is
# stale. Docs/lessons/assumptions have their own rules in consolidate().
KIND_STALE_DAYS = {"fact": 365, "command": 180, "contract": 365}


@dataclass(frozen=True)
class RecallQuery:
    text: str
    scope: str | None = None
    kinds: tuple[str, ...] | None = None
    limit: int = 8
    budget_tokens: int = 2000
    per_kind_budget: dict[str, int] | None = None
    allow_untrusted: bool = False


@dataclass
class RecallHit:
    item: KnowledgeItem
    score: float
    snippet: str
    citation: str
    reasons: dict[str, float]


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
        actor: str = "system",
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
        if item.trust not in VALID_TRUST:
            raise ValueError(f"invalid trust: {item.trust}")
        if item.sensitivity == "secret" and not allow_secret:
            raise ValueError("secret knowledge is not stored by default")
        if not allow_secret and any(
            pattern.search(item.title) or pattern.search(item.body)
            for pattern in SECRET_PATTERNS
        ):
            raise ValueError("refusing to store likely secret material in knowledge")
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
        if actor == "agent" and item.status == "active" and item.kind in {
            "preference",
            "decision",
            "fact",
            "lesson",
            "assumption",
        }:
            raise ValueError(
                f"{item.kind} written by the agent must be candidate; "
                "only a user/replay-validated path can activate it"
            )
        if item.kind == "assumption" and not item.expires_at:
            item.expires_at = (
                datetime.now(timezone.utc) + timedelta(days=30)
            ).isoformat(timespec="microseconds")

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
            with self.db.transaction():
                self.db.execute(
                    """
                    UPDATE knowledge_items
                    SET status = ?, tags_json = ?, source_json = ?, confidence = MAX(confidence, ?),
                        severity = ?, validation_json = ?, expires_at = COALESCE(?, expires_at),
                        source_hash = COALESCE(?, source_hash),
                        trust = CASE WHEN trust = 'trusted' THEN 'trusted' ELSE ? END,
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
                        item.source_hash,
                        item.trust,
                        utcnow(),
                        utcnow(),
                        existing_item.id,
                    ),
                )
                if self._fts:
                    self.db.execute(
                        "UPDATE knowledge_fts SET tags = ? WHERE item_id = ?",
                        (" ".join(tags), existing_item.id),
                    )
            return existing_item.id

        now = utcnow()
        item.id = item.id or new_id("kn")
        item.created_at = item.created_at or now
        item.updated_at = now
        item.freshness_at = item.freshness_at or now
        with self.db.transaction():
            self.db.execute(
                """
                INSERT INTO knowledge_items (
                  id, tenant_id, project_id, owner_id, scope, kind, status, title, body,
                  tags_json, source_json, confidence, severity, validation_json,
                  freshness_at, expires_at, supersedes_json, sensitivity, source_hash,
                  trust, dedupe_key, use_count, last_used_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
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
                    item.source_hash,
                    item.trust,
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
                        estimate_tokens(chunk),
                        now,
                    ),
                )

            if self._fts:
                self.db.execute(
                    "INSERT INTO knowledge_fts(item_id, title, body, tags) VALUES (?, ?, ?, ?)",
                    (item.id, item.title, item.body, " ".join(item.tags)),
                )
        return item.id

    def refresh_sources(self, workspace_root: str | Path | None = None) -> dict[str, int]:
        """Hash file-backed docs/commands/contracts and stale changed sources."""
        if workspace_root is None:
            return {"checked": 0, "stale": 0}
        root = Path(workspace_root).resolve()
        rows = self.db.fetchall(
            "SELECT * FROM knowledge_items WHERE tenant_id = ? AND status = 'active' "
            "AND kind IN ('doc', 'command', 'contract') "
            "AND (scope != 'project' OR project_id = ?)",
            (self.tenant_id, self.project_id),
        )
        checked = 0
        stale = 0
        for row in rows:
            item = self._row_to_item(row)
            if item.source.kind != "file" or not item.source.ref:
                continue
            path = Path(item.source.ref)
            if not path.is_absolute():
                path = root / path
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            checked += 1
            if item.source_hash and item.source_hash != digest:
                self.db.execute(
                    "UPDATE knowledge_items SET status = 'stale', source_hash = ?, "
                    "updated_at = ? WHERE id = ?",
                    (digest, utcnow(), item.id),
                )
                stale += 1
            elif not item.source_hash:
                self.db.execute(
                    "UPDATE knowledge_items SET source_hash = ? WHERE id = ?",
                    (digest, item.id),
                )
        if checked:
            self.db.commit()
        return {"checked": checked, "stale": stale}

    def capture_turn(
        self,
        session_id: str,
        turn_id: str,
        user_message: str,
        assistant_message: str = "",
    ) -> str | None:
        """Create a candidate fact when the user explicitly corrects/prefers.

        This is deliberately conservative: only explicit correction/preference
        phrases are captured, and only as `candidate`. It never writes active
        knowledge and never runs an extra model call.
        """
        text = (user_message or "").strip()
        if not text:
            return None
        marker = re.search(
            r"(记住|以后都|以后请|不是这样|不对|应该是|不要用|"
            r"remember|prefer|instead|actually|do not|don't)",
            text,
            re.IGNORECASE,
        )
        if not marker:
            return None
        title = " ".join(text.split())[:80]
        item = KnowledgeItem(
            title=title or "Captured preference",
            body=text[:2000],
            kind="fact",
            scope="project",
            status="candidate",
            project_id=self.project_id,
            confidence=0.55,
            trust="derived",
            validation={"method": "rule-capture", "status": "unvalidated"},
            source=SourceRef(kind="session", ref=f"{session_id}:{turn_id}"),
        )
        return self.write(item, actor="agent")

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
            "AND (scope != 'project' OR project_id = ?) AND source_json LIKE ? ESCAPE '\\' LIMIT 1",
            (
                self.tenant_id,
                kind,
                self.project_id,
                f'%"{self._escape_like(ref)}"%',
            ),
        )
        return self._row_to_item(row) if row else None

    def search(
        self,
        query: str,
        scope: str | None = None,
        kinds: Iterable[str] | None = None,
        limit: int = 8,
        budget_tokens: int = 2000,
        record_usage: bool = True,
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
                  AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\'
                       OR tags_json LIKE ? ESCAPE '\\')
            """
            like = f"%{self._escape_like(query)}%"
            params = [self.tenant_id, like, like, like]
            sql, params = self._apply_filters(sql, params, scope, kinds_list)
            sql += " ORDER BY confidence DESC, updated_at DESC LIMIT ?"
            params.append(max(limit * 3, limit))
            for row in self.db.fetchall(sql, params):
                rows.append((row, 0.0))

        hits_by_id: dict[str, KnowledgeHit] = {}
        for row, rank in rows:
            item = self._row_to_item(row)
            hits_by_id[item.id] = KnowledgeHit(
                item=item,
                score=(-rank) if rank else float(item.confidence),
                snippet=self._snippet(item.body, query),
                origin="fts" if rank else "like",
            )

        for chunk_hit in self._chunk_hits(
            query, scope, kinds_list, limit=max(limit * 3, 24)
        ):
            existing = hits_by_id.get(chunk_hit.item.id)
            if existing is None:
                hits_by_id[chunk_hit.item.id] = chunk_hit
                continue
            if chunk_hit.score > existing.score:
                existing.score = chunk_hit.score
            existing.origin = "hybrid"
            existing.snippet = chunk_hit.snippet
            existing.chunk_id = chunk_hit.chunk_id

        hits: list[KnowledgeHit] = []
        used = 0
        for hit in sorted(hits_by_id.values(), key=lambda value: value.score, reverse=True):
            cost = estimate_tokens(hit.item.body)
            if hits and used + cost > budget_tokens:
                continue
            used += cost
            hits.append(hit)
            if len(hits) >= limit:
                break

        if hits and record_usage:
            ids = [h.item.id for h in hits]
            placeholders = ",".join("?" for _ in ids)
            self.db.execute(
                f"UPDATE knowledge_items SET use_count = use_count + 1, last_used_at = ? "
                f"WHERE id IN ({placeholders})",
                [utcnow(), *ids],
            )
            self.db.commit()
        return hits

    def _chunk_hits(
        self,
        query: str,
        scope: str | None,
        kinds: list[str] | None,
        limit: int = 24,
    ) -> list[KnowledgeHit]:
        terms = [
            term
            for term in re.findall(r"[\w\u4e00-\u9fff]+", query)
            if len(term) > 1
        ][:8]
        if not terms:
            return []
        clauses: list[str] = []
        params: list[object] = [self.tenant_id]
        for term in terms:
            clauses.append("c.content LIKE ? ESCAPE '\\'")
            params.append(f"%{self._escape_like(term)}%")
        sql = f"""
            SELECT ki.*, c.id AS chunk_id, c.content AS chunk_content
            FROM knowledge_chunks c
            JOIN knowledge_items ki ON ki.id = c.item_id
            WHERE ki.tenant_id = ? AND ki.status = 'active'
              AND ({' OR '.join(clauses)})
        """
        sql, params = self._apply_filters(
            sql, params, scope, kinds, prefix="ki."
        )
        sql += " LIMIT ?"
        params.append(max(limit * 2, 48))
        best: dict[str, KnowledgeHit] = {}
        for row in self.db.fetchall(sql, params):
            item = self._row_to_item(row)
            content = str(row["chunk_content"] or "")
            lower = content.lower()
            hits = sum(lower.count(term.lower()) for term in terms)
            score = max(1.0, float(hits)) * 0.75
            current = best.get(item.id)
            if current is not None and current.score >= score:
                continue
            best[item.id] = KnowledgeHit(
                item=item,
                score=score,
                snippet=self._snippet(content, query),
                origin="chunk",
                chunk_id=str(row["chunk_id"]),
            )
        return sorted(best.values(), key=lambda value: value.score, reverse=True)[:limit]

    def recall(self, query: RecallQuery | str) -> list[RecallHit]:
        """Return a task-aware, budgeted memory window with citations.

        `search()` remains the raw retrieval primitive. `recall()` adds the
        ranking, per-kind budgets and citation formatting used by the prompt.
        """
        if isinstance(query, str):
            query = RecallQuery(text=query)
        text = query.text.strip()
        if not text:
            items = [
                item
                for item in self.list(limit=query.limit)
                if query.allow_untrusted or item.trust != "untrusted"
            ]
            return [
                RecallHit(
                    item=item,
                    score=float(item.confidence),
                    snippet=item.body[:240],
                    citation=self._citation(item),
                    reasons={"empty_query": 1.0},
                )
                for item in items
            ]

        candidates = self.search(
            text,
            scope=query.scope,
            kinds=query.kinds,
            limit=max(query.limit * 4, 24),
            budget_tokens=max(query.budget_tokens * 4, 8000),
            record_usage=False,
        )
        if not candidates:
            return []

        max_score = max((hit.score for hit in candidates), default=1.0) or 1.0
        now = datetime.now(timezone.utc)
        per_kind = query.per_kind_budget or DEFAULT_KIND_BUDGETS
        ranked: list[tuple[float, RecallHit]] = []
        for hit in candidates:
            item = hit.item
            if item.trust == "untrusted" and not query.allow_untrusted:
                continue
            relevance = min(1.0, max(0.05, float(hit.score) / max_score))
            confidence = min(1.0, max(0.0, item.confidence))
            freshness = self._freshness(item, now)
            severity = SEVERITY_WEIGHT.get(item.severity, 1.0)
            score = (relevance**1.2) * confidence * freshness * severity
            ranked.append(
                (
                    score,
                    RecallHit(
                        item=item,
                        score=score,
                        snippet=hit.snippet,
                        citation=self._citation(item),
                        reasons={
                            "relevance": relevance,
                            "confidence": confidence,
                            "freshness": freshness,
                            "severity": severity,
                        },
                    ),
                )
            )

        selected: list[RecallHit] = []
        used = 0
        kind_used: dict[str, int] = {}
        seen: set[str] = set()
        for _score, recall_hit in sorted(ranked, key=lambda pair: pair[0], reverse=True):
            if recall_hit.item.id in seen:
                continue
            cost = estimate_tokens(recall_hit.item.body)
            kind = recall_hit.item.kind
            kind_budget = per_kind.get(kind, 800)
            if selected and used + cost > query.budget_tokens:
                continue
            if selected and kind_used.get(kind, 0) + cost > kind_budget:
                continue
            seen.add(recall_hit.item.id)
            selected.append(recall_hit)
            used += cost
            kind_used[kind] = kind_used.get(kind, 0) + cost
            if len(selected) >= query.limit:
                break

        if selected:
            ids = [hit.item.id for hit in selected]
            placeholders = ",".join("?" for _ in ids)
            self.db.execute(
                f"UPDATE knowledge_items SET use_count = use_count + 1, last_used_at = ? "
                f"WHERE id IN ({placeholders})",
                [utcnow(), *ids],
            )
            self.db.commit()
        return selected

    def _freshness(self, item: KnowledgeItem, now: datetime) -> float:
        value = item.freshness_at or item.updated_at
        try:
            created = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return 0.8
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - created).total_seconds() / 86400.0)
        half_life = KIND_HALF_LIFE_DAYS.get(item.kind, 365)
        return math.exp(-age_days / half_life)

    @staticmethod
    def _citation(item: KnowledgeItem) -> str:
        source = f"{item.source.kind}:{item.source.ref}" if item.source.ref else item.source.kind
        return (
            f"[{item.id}] {item.kind} · {source} · confidence {item.confidence:.2f} "
            f"· trust {item.trust}"
        )

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

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
        prefix: str | None = None,
    ) -> tuple[str, list[object]]:
        if prefix is None:
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
            "SELECT confidence, status FROM knowledge_items WHERE id = ? AND tenant_id = ? "
            "AND (scope != 'project' OR project_id = ?)",
            (item_id, self.tenant_id, self.project_id),
        )
        if not row:
            return False
        confidence = float(row["confidence"] or 0.8)
        current = str(row["status"] or "active")
        if outcome in {"effective", "used", "good"}:
            confidence = min(1.0, confidence + 0.02)
            # A quarantined item must be re-validated before recall; a
            # candidate must be promoted. Positive feedback alone cannot
            # undo either gate.
            if current in {"quarantined", "candidate"}:
                status = current
            else:
                status = "active"
        elif outcome in {"ineffective", "bad", "wrong"}:
            confidence = max(0.0, confidence - 0.15)
            if confidence < 0.3:
                status = "quarantined"
            elif current in {"candidate", "quarantined"}:
                status = current
            else:
                status = "active"
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

    def promote(
        self,
        item_id: str,
        validation: dict[str, Any] | None = None,
        actor: str = "agent",
    ) -> bool:
        if not _validation_passed(validation):
            return False
        if actor == "agent" and str((validation or {}).get("method") or "") not in {
            "replay",
            "test",
        }:
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
        stale_lessons = self.db.execute(
            "UPDATE knowledge_items SET status = 'stale', updated_at = ? "
            "WHERE tenant_id = ? AND (scope != 'project' OR project_id = ?) "
            "AND status = 'active' AND kind = 'lesson' AND use_count = 0 "
            "AND COALESCE(last_used_at, updated_at) < ?",
            (now_iso, self.tenant_id, self.project_id, cutoff_stale),
        ).rowcount
        stale_items = 0
        for kind, days in KIND_STALE_DAYS.items():
            cutoff = (now - timedelta(days=days)).isoformat(timespec="microseconds")
            stale_items += self.db.execute(
                "UPDATE knowledge_items SET status = 'stale', updated_at = ? "
                "WHERE tenant_id = ? AND (scope != 'project' OR project_id = ?) "
                "AND status = 'active' AND kind = ? AND use_count = 0 "
                "AND COALESCE(last_used_at, updated_at) < ?",
                (now_iso, self.tenant_id, self.project_id, kind, cutoff),
            ).rowcount
        self.db.commit()
        return {
            "expired": int(expired or 0),
            "stale_docs": int(stale_docs or 0),
            "stale_lessons": int(stale_lessons or 0),
            "stale_items": int(stale_items or 0),
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

        def loads(value: object, default: Any) -> Any:
            if not value:
                return default
            try:
                return json.loads(str(value))
            except (TypeError, ValueError):
                return default

        source_data = loads(row["source_json"], {})
        tags = loads(row["tags_json"], [])
        validation = loads(row["validation_json"], None)
        if not isinstance(source_data, dict):
            source_data = {}
        if not isinstance(tags, list):
            tags = []
        if not isinstance(validation, dict):
            validation = None
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
            source_hash=row["source_hash"] if "source_hash" in row.keys() else None,
            trust=(row["trust"] if "trust" in row.keys() else "trusted") or "trusted",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            use_count=int(row["use_count"] or 0),
            last_used_at=row["last_used_at"],
        )
