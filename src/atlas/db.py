from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  root        TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,
  project_id  TEXT NOT NULL,
  title       TEXT,
  status      TEXT NOT NULL DEFAULT 'idle',
  model       TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  seq         INTEGER NOT NULL,
  role        TEXT NOT NULL,
  content     TEXT,
  status      TEXT NOT NULL DEFAULT 'done',
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, seq);

CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL,
  turn_id     TEXT,
  type        TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);

CREATE TABLE IF NOT EXISTS checkpoints (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  turn_id     TEXT,
  state_json  TEXT NOT NULL,
  git_sha     TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON checkpoints(session_id, created_at);

CREATE TABLE IF NOT EXISTS plans (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  content     TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'draft',
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  plan_id     TEXT,
  subject     TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',
  depends_on_json TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verifications (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  task_id     TEXT,
  kind        TEXT NOT NULL,
  command     TEXT NOT NULL,
  cwd         TEXT NOT NULL,
  exit_code   INTEGER NOT NULL,
  stdout_hash TEXT,
  stdout      TEXT,
  stderr      TEXT,
  duration_ms INTEGER NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verifications_session ON verifications(session_id, created_at);

CREATE TABLE IF NOT EXISTS approvals (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  turn_id     TEXT,
  tool        TEXT NOT NULL,
  request_json TEXT NOT NULL,
  decision    TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id          TEXT PRIMARY KEY,
  type        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'queued',
  payload_json TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  model       TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cost_usd    REAL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  kind        TEXT NOT NULL,
  path        TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_items (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL DEFAULT 'local',
  project_id      TEXT NOT NULL DEFAULT '',
  owner_id        TEXT,
  scope           TEXT NOT NULL,
  kind            TEXT NOT NULL,
  status          TEXT NOT NULL,
  title           TEXT NOT NULL,
  body            TEXT NOT NULL,
  tags_json       TEXT,
  source_json     TEXT NOT NULL,
  confidence      REAL DEFAULT 0.8,
  severity        TEXT DEFAULT 'medium',
  validation_json TEXT,
  freshness_at    TEXT,
  expires_at      TEXT,
  supersedes_json TEXT,
  sensitivity     TEXT DEFAULT 'internal',
  dedupe_key      TEXT,
  use_count       INTEGER DEFAULT 0,
  last_used_at    TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_scope
  ON knowledge_items(tenant_id, project_id, scope, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_kind
  ON knowledge_items(project_id, kind, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_dedupe
  ON knowledge_items(tenant_id, project_id, scope, kind, dedupe_key);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id            TEXT PRIMARY KEY,
  item_id       TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
  project_id    TEXT NOT NULL DEFAULT '',
  ordinal       INTEGER NOT NULL,
  content       TEXT NOT NULL,
  symbol        TEXT,
  start_line    INTEGER,
  end_line      INTEGER,
  token_count   INTEGER,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_item ON knowledge_chunks(item_id);

CREATE TABLE IF NOT EXISTS knowledge_links (
  from_id     TEXT NOT NULL,
  to_id       TEXT NOT NULL,
  relation    TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (from_id, to_id, relation)
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self.conn is not None:
            return self.conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        self.conn = conn
        return conn

    def initialize(self) -> None:
        conn = self.connect()
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.connect().execute(sql, tuple(params))

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return list(self.execute(sql, params).fetchall())

    def commit(self) -> None:
        self.connect().commit()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def table_names(self) -> list[str]:
        rows = self.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [r["name"] for r in rows]

    def fts5_available(self) -> bool:
        try:
            self.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _atlas_fts_probe USING fts5(x)")
            self.execute("DROP TABLE IF EXISTS _atlas_fts_probe")
            self.commit()
            return True
        except Exception:
            try:
                self.connect().rollback()
            except Exception:
                pass
            return False
