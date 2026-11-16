from __future__ import annotations

import hashlib
import json
from typing import Any

from .db import Database
from .models import new_id, utcnow


class SessionStore:
    def __init__(self, db: Database):
        self.db = db

    def create(self, project_id: str, title: str | None = None, model: str | None = None) -> dict[str, Any]:
        session_id = new_id("ses")
        now = utcnow()
        self.db.execute(
            "INSERT INTO sessions(id, project_id, title, status, model, created_at, updated_at) "
            "VALUES (?, ?, ?, 'idle', ?, ?, ?)",
            (session_id, project_id, title or "Untitled", model, now, now),
        )
        self.db.commit()
        return self.get(session_id) or {}

    def get(self, session_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return dict(row) if row else None

    def list(self, project_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if project_id:
            rows = self.db.fetchall(
                "SELECT * FROM sessions WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
                (project_id, limit),
            )
        else:
            rows = self.db.fetchall(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in rows]

    def append_turn(self, session_id: str, role: str, content: str) -> str:
        row = self.db.fetchone(
            "SELECT COALESCE(MAX(seq), 0) AS n FROM turns WHERE session_id = ?",
            (session_id,),
        )
        seq = int(row["n"]) + 1 if row else 1
        turn_id = new_id("trn")
        now = utcnow()
        self.db.execute(
            "INSERT INTO turns(id, session_id, seq, role, content, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'done', ?)",
            (turn_id, session_id, seq, role, content, now),
        )
        self.db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
        )
        self.db.commit()
        return turn_id

    def turns(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY seq ASC LIMIT ?",
            (session_id, limit),
        )
        return [dict(r) for r in rows]

    def event(self, session_id: str, turn_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO events(session_id, turn_id, type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, turn_id, event_type, json.dumps(payload, ensure_ascii=False), utcnow()),
        )
        self.db.commit()

    def events(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM events WHERE session_id = ? ORDER BY id ASC LIMIT ?",
            (session_id, limit),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result

    def save_checkpoint(
        self,
        session_id: str,
        turn_id: str | None,
        state: dict[str, Any],
        git_sha: str | None = None,
    ) -> str:
        checkpoint_id = new_id("ck")
        self.db.execute(
            "INSERT INTO checkpoints(id, session_id, turn_id, state_json, git_sha, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                checkpoint_id,
                session_id,
                turn_id,
                json.dumps(state, ensure_ascii=False),
                git_sha,
                utcnow(),
            ),
        )
        self.db.commit()
        return checkpoint_id

    def latest_checkpoint(self, session_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM checkpoints WHERE session_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id,),
        )
        if not row:
            return None
        item = dict(row)
        item["state"] = json.loads(item.pop("state_json") or "{}")
        return item

    def load_state(self, session_id: str) -> dict[str, Any] | None:
        checkpoint = self.latest_checkpoint(session_id)
        if checkpoint:
            state = dict(checkpoint["state"])
            messages = list(state.get("messages") or [])
            # Include turns committed after the checkpoint (e.g. the assistant
            # message written just before a crash). Avoid duplicating the tail.
            for turn in self.turns(session_id):
                if turn["created_at"] <= checkpoint["created_at"] or not turn["content"]:
                    continue
                candidate = {"role": turn["role"], "content": turn["content"]}
                if candidate not in messages[-4:]:
                    messages.append(candidate)
            state["messages"] = messages
            return state
        turns = self.turns(session_id)
        if not turns:
            return None
        return {
            "messages": [
                {"role": t["role"], "content": t["content"]}
                for t in turns
                if t["content"]
            ]
        }

    def record_verification(
        self,
        session_id: str,
        kind: str,
        command: str,
        cwd: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        duration_ms: int,
    ) -> str:
        verification_id = new_id("ev")
        stdout_hash = hashlib.sha256(stdout.encode("utf-8")).hexdigest()
        self.db.execute(
            "INSERT INTO verifications(id, session_id, task_id, kind, command, cwd, exit_code, "
            "stdout_hash, stdout, stderr, duration_ms, created_at) "
            "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                verification_id,
                session_id,
                kind,
                command,
                cwd,
                exit_code,
                stdout_hash,
                stdout,
                stderr,
                duration_ms,
                utcnow(),
            ),
        )
        self.db.commit()
        return verification_id

    def record_usage(self, session_id: str, model: str, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        self.db.execute(
            "INSERT INTO usage(id, session_id, model, input_tokens, output_tokens, cost_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id("usg"), session_id, model, input_tokens, output_tokens, cost_usd, utcnow()),
        )
        self.db.commit()

    def save_plan(self, session_id: str, content: str) -> str:
        plan_id = new_id("pln")
        self.db.execute(
            "INSERT INTO plans(id, session_id, content, status, created_at) "
            "VALUES (?, ?, ?, 'draft', ?)",
            (plan_id, session_id, content, utcnow()),
        )
        self.db.commit()
        return plan_id
