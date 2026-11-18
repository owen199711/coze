from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

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
  source_hash     TEXT,
  trust           TEXT NOT NULL DEFAULT 'trusted',
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
        conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        self.conn = conn
        return conn

    def initialize(self) -> None:
        conn = self.connect()
        conn.executescript(SCHEMA)
        self._migrate_knowledge_columns(conn)
        self._repair_duplicate_turn_seqs(conn)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_session_seq ON turns(session_id, seq)"
        )
        conn.execute("PRAGMA user_version = 2")
        conn.commit()

    @staticmethod
    def _migrate_knowledge_columns(conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(knowledge_items)").fetchall()
        }
        if "source_hash" not in columns:
            conn.execute("ALTER TABLE knowledge_items ADD COLUMN source_hash TEXT")
        if "trust" not in columns:
            conn.execute(
                "ALTER TABLE knowledge_items ADD COLUMN trust TEXT NOT NULL DEFAULT 'trusted'"
            )

    @staticmethod
    def _repair_duplicate_turn_seqs(conn: sqlite3.Connection) -> None:
        """Renumber duplicate (session_id, seq) rows before the unique index.

        Older databases created before the unique index can contain duplicates
        from concurrent writers. Order by created_at/rowid so recovery keeps a
        deterministic order, then add the constraint.
        """
        duplicated = conn.execute(
            "SELECT session_id FROM turns GROUP BY session_id, seq HAVING COUNT(*) > 1"
        ).fetchall()
        for row in duplicated:
            session_id = row["session_id"]
            ids = conn.execute(
                "SELECT id FROM turns WHERE session_id = ? ORDER BY created_at, rowid",
                (session_id,),
            ).fetchall()
            for seq, item in enumerate(ids, start=1):
                conn.execute("UPDATE turns SET seq = ? WHERE id = ?", (seq, item["id"]))

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.connect().execute(sql, tuple(params))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a group of writes atomically across the shared connection."""
        conn = self.connect()
        if conn.in_transaction:
            conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

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
