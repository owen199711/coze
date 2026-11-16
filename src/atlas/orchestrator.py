from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable

from .agent import AgentRuntime
from .config import AtlasConfig, load_config, write_default_config
from .db import Database
from .knowledge import KnowledgeService
from .llm import OpenAICompatibleClient
from .models import KnowledgeItem, SourceRef, utcnow
from .sessions import SessionStore
from .workspace import Workspace


def project_id_for(root: str | Path) -> str:
    digest = hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:12]
    return f"proj_{digest}"


class Atlas:
    def __init__(self, root: str | Path = "."):
        self.config: AtlasConfig = load_config(root)
        self.config.ensure_dirs()
        self.db = Database(self.config.db_path)
        self.db.initialize()
        self.project_id = project_id_for(self.config.root)
        self._ensure_project()
        self.sessions = SessionStore(self.db)
        self.knowledge = KnowledgeService(self.db, self.project_id)
        self.workspace = Workspace(self.config.root, self.config)
        self._model: OpenAICompatibleClient | None = None

    def _ensure_project(self) -> None:
        row = self.db.fetchone("SELECT id FROM projects WHERE id = ?", (self.project_id,))
        if not row:
            self.db.execute(
                "INSERT INTO projects(id, name, root, created_at) VALUES (?, ?, ?, ?)",
                (self.project_id, self.config.name, str(self.config.root), utcnow()),
            )
            self.db.commit()

    # ------------------------------------------------------------- lifecycle
    @classmethod
    def init(cls, root: str | Path = ".", name: str | None = None) -> "Atlas":
        write_default_config(root, name=name)
        atlas = cls(root)
        return atlas

    def create_session(self, title: str | None = None) -> dict[str, Any]:
        return self.sessions.create(
            self.project_id, title or "New session", self.config.model_name or None
        )

    def session(self, session_id: str) -> dict[str, Any] | None:
        session = self.sessions.get(session_id)
        if session and session.get("project_id") != self.project_id:
            return None
        return session

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.sessions.list(project_id=self.project_id, limit=limit)

    # ------------------------------------------------------------------ chat
    @property
    def model(self) -> OpenAICompatibleClient | None:
        if self._model is not None:
            return self._model
        if not self.config.model_name and not os.environ.get("ATLAS_MODEL"):
            return None
        self._model = OpenAICompatibleClient.from_config(self.config)
        return self._model

    def chat(
        self,
        session_id: str,
        message: str,
        ask_user: Callable[[str, list[str], str | None], str] | None = None,
        *,
        on_text: Callable[[str], None] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> str:
        if not self.session(session_id):
            raise ValueError(f"session not found in this project: {session_id}")
        runtime = AgentRuntime(
            config=self.config,
            workspace=self.workspace,
            knowledge=self.knowledge,
            sessions=self.sessions,
            model=self.model,
            ask_user=ask_user,
        )
        return runtime.run(
            session_id,
            message,
            on_text=on_text,
            on_event=on_event,
        )

    def compact(self, session_id: str, keep_recent: int = 8) -> dict[str, Any]:
        if not self.session(session_id):
            raise ValueError(f"session not found in this project: {session_id}")
        runtime = AgentRuntime(
            config=self.config,
            workspace=self.workspace,
            knowledge=self.knowledge,
            sessions=self.sessions,
            model=self.model,
        )
        return runtime.compact(session_id, keep_recent=keep_recent)

    # ------------------------------------------------------------- knowledge
    def remember(
        self,
        title: str,
        body: str,
        kind: str = "fact",
        scope: str = "project",
        source: str | None = None,
    ) -> str:
        item = KnowledgeItem(
            title=title,
            body=body,
            kind=kind,
            scope=scope,
            status="candidate" if kind == "lesson" else "active",
            project_id=self.project_id,
            source=SourceRef(kind="session", ref=source or "manual"),
        )
        return self.knowledge.write(item)

    def knowledge_search(self, query: str, limit: int = 8):
        return self.knowledge.search(query, limit=limit)

    # ----------------------------------------------------------------- doctor
    def doctor(self) -> dict[str, Any]:
        tables = self.db.table_names()
        base_tables = [t for t in tables if not t.startswith("knowledge_fts")]
        return {
            "root": str(self.config.root),
            "db": str(self.config.db_path),
            "base_tables": len(base_tables),
            "tables": len(base_tables),
            "fts_tables": len(tables) - len(base_tables),
            "fts5": self.db.fts5_available(),
            "git": self.workspace.git_sha() is not None,
            "model_configured": bool(self.config.model_name or os.environ.get("ATLAS_MODEL")),
            "model": self.config.model_name or os.environ.get("ATLAS_MODEL") or "(not configured)",
            "api_key_env": self.config.api_key_env,
            "test_command": self.config.test_command,
            "knowledge_items": sum(
                sum(v.values()) for v in self.knowledge.stats().values()
            ),
        }

    def close(self) -> None:
        self.db.close()
