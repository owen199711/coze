from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class SourceRef:
    kind: str = "session"  # session | file | url | command | decision
    ref: str = ""
    quote: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "quote": self.quote}


@dataclass
class KnowledgeItem:
    """A single item in the unified knowledge store.

    `kind` carries the old distinctions:
    - preference / fact / decision / doc / command / contract / assumption
    - lesson: a validated behaviour rule (requires validation evidence)
    """

    title: str
    body: str
    kind: str = "fact"
    scope: str = "project"  # user | project | org
    status: str = "active"  # candidate | active | stale | retired | quarantined
    project_id: str | None = None
    tenant_id: str = "local"
    owner_id: str | None = None
    id: str = field(default_factory=lambda: new_id("kn"))
    tags: list[str] = field(default_factory=list)
    source: SourceRef = field(default_factory=SourceRef)
    confidence: float = 0.8
    severity: str = "medium"  # low | medium | high
    validation: dict[str, Any] | None = None
    freshness_at: str = field(default_factory=utcnow)
    expires_at: str | None = None
    sensitivity: str = "internal"  # public | internal | secret
    source_hash: str | None = None
    trust: str = "trusted"  # trusted | derived | untrusted
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    use_count: int = 0
    last_used_at: str | None = None


@dataclass
class ToolResult:
    status: str  # ok | error
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    artifact: str | None = None

    def as_text(self, max_chars: int = 4000) -> str:
        import json

        payload = {
            "status": self.status,
            "summary": self.summary,
            "data": self.data,
            "artifact": self.artifact,
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if len(text) <= max_chars:
            return text
        # Never drop the useful part of a large result. The old fallback only
        # returned data_keys, which made the model retry the same read/search
        # tool call until it exhausted max_turns. Keep a bounded preview instead.
        preview: dict[str, Any] = {}
        remaining = max(600, max_chars - 900)
        for key in ("content", "text", "stdout", "stderr"):
            value = self.data.get(key)
            if not isinstance(value, str) or not value or remaining <= 0:
                continue
            limit = min(remaining, 2600)
            snippet = value[:limit]
            if len(value) > limit:
                snippet += "\n...[truncated]"
            preview[key] = snippet
            remaining -= len(snippet)
        matches = self.data.get("matches")
        if isinstance(matches, list):
            preview["matches"] = matches[:20]
        for key in ("path", "start", "end", "total_lines", "truncated", "exit_code", "duration_ms"):
            if key in self.data:
                preview[key] = self.data[key]
        compact = {
            "status": self.status,
            "summary": self.summary,
            "data_preview": preview,
            "artifact": self.artifact,
            "note": f"truncated preview: full result is {len(text)} chars",
        }
        result = json.dumps(compact, ensure_ascii=False, indent=2)
        if len(result) <= max_chars:
            return result
        # Last resort: keep only the envelope, never partial invalid JSON.
        compact["data_preview"] = {"keys": list(self.data.keys())}
        compact["note"] = f"data omitted: {len(text)} chars > {max_chars}"
        return json.dumps(compact, ensure_ascii=False, indent=2)


@dataclass
class Verification:
    id: str
    session_id: str
    kind: str
    command: str
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    created_at: str = field(default_factory=utcnow)
