from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .config import AtlasConfig
from .knowledge import KnowledgeService
from .models import KnowledgeItem, SourceRef, ToolResult
from .workspace import Workspace, is_public_http_url


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[..., ToolResult]
    read_only: bool = False

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }


class AtlasTools:
    """The 12-tool surface from Lean Atlas v1.

    M0 implements read/edit/search/shell/git/check/knowledge/plan/ask_user/web(fetch).
    lsp/subagent return a clear "not available in this milestone" error.
    """

    def __init__(
        self,
        workspace: Workspace,
        knowledge: KnowledgeService,
        config: AtlasConfig,
        session_id: str,
        ask_user: Callable[[str, list[str], str | None], str] | None = None,
    ):
        self.workspace = workspace
        self.knowledge = knowledge
        self.config = config
        self.session_id = session_id
        self.ask_user_cb = ask_user
        self.plan_path = config.plans_dir / f"{session_id}.md"
        self.permission_mode = config.permission_mode
        self._read_paths: set[str] = set()
        self._specs = self._build()

    def _path_key(self, path: str) -> str:
        try:
            resolved = self.workspace.resolve(path)
            return resolved.relative_to(self.workspace.root).as_posix()
        except (OSError, ValueError):
            return path

    def _explore_denied(self, action: str) -> ToolResult | None:
        if self.permission_mode == "explore":
            return ToolResult(
                "error",
                f"{action} is disabled in explore mode; switch permissions.mode to "
                "accept_edits, dont_ask, or default",
            )
        return None

    def _approval_denied(self, spec: ToolSpec, args: dict[str, Any]) -> ToolResult | None:
        """Gate non-read-only tools in `default` mode (ADR-0003).

        `accept_edits` and `dont_ask` skip approval; `explore` is handled by the
        per-handler checks. In `default` mode a write requires an explicit yes
        from the user; without an interactive callback we fail closed.
        """
        if self.permission_mode != "default" or spec.read_only:
            return None
        if not self.ask_user_cb:
            return ToolResult(
                "error",
                f"{spec.name} requires approval in default mode but no interactive "
                "input is available; switch permission_mode to accept_edits or dont_ask",
                {"tool": spec.name},
            )
        question = f"Allow {spec.name} with arguments {json.dumps(args, ensure_ascii=False)}?"
        answer = self.ask_user_cb(question, ["yes", "no"], "no")
        if str(answer).strip().lower() not in {"yes", "y", "allow", "approve"}:
            return ToolResult(
                "error",
                f"{spec.name} was not approved by the user",
                {"tool": spec.name, "answer": answer},
            )
        return None

    def _build(self) -> dict[str, ToolSpec]:
        specs = [
            ToolSpec(
                "read",
                "Read a text file, optionally a line range. Paths are relative to the workspace.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start": {"type": "integer", "minimum": 1},
                        "end": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                },
                self._read,
                read_only=True,
            ),
            ToolSpec(
                "edit",
                "Create or edit a file. mode=create|replace|replace_text. replace_text requires unique old text.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "mode": {"type": "string", "enum": ["create", "replace", "replace_text"]},
                        "content": {"type": "string"},
                        "old": {"type": "string"},
                        "new": {"type": "string"},
                    },
                    "required": ["path", "mode"],
                },
                self._edit,
            ),
            ToolSpec(
                "search",
                "Search files or content. mode=files|content. Files mode takes a glob; content mode takes a regex.",
                {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["files", "content"]},
                        "query": {"type": "string"},
                        "glob": {"type": "string"},
                    },
                    "required": ["mode", "query"],
                },
                self._search,
                read_only=True,
            ),
            ToolSpec(
                "shell",
                "Run a command in the workspace (sandbox policy applies). Use for builds and one-off commands.",
                {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 3600},
                    },
                    "required": ["command"],
                },
                self._shell,
            ),
            ToolSpec(
                "git",
                "Run a restricted git command: status/diff/log/branch/commit. Force-push and hard reset are denied.",
                {
                    "type": "object",
                    "properties": {"args": {"type": "string"}},
                    "required": ["args"],
                },
                self._git,
            ),
            ToolSpec(
                "check",
                "Run a configured check: test/lint/typecheck/format, or an explicit command.",
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["test", "lint", "typecheck", "format"]},
                        "command": {"type": "string"},
                    },
                },
                self._check,
            ),
            ToolSpec(
                "knowledge",
                "Search, read, write, validate, or give feedback on project knowledge. "
                "action=search|get|write|validate|feedback.",
                {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["search", "get", "write", "validate", "feedback"],
                        },
                        "query": {"type": "string"},
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "kind": {"type": "string"},
                        "scope": {"type": "string"},
                        "source": {"type": "string"},
                        "outcome": {"type": "string"},
                        "validation": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["action"],
                },
                self._knowledge,
            ),
            ToolSpec(
                "plan",
                "Read or write the current session plan. action=read|write|exit.",
                {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["read", "write", "exit"]},
                        "content": {"type": "string"},
                    },
                    "required": ["action"],
                },
                self._plan,
            ),
            ToolSpec(
                "ask_user",
                "Ask the user a short question with 2-4 options and a recommended default.",
                {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "options": {"type": "array", "items": {"type": "string"}},
                        "recommended": {"type": "string"},
                    },
                    "required": ["question", "options"],
                },
                self._ask,
                read_only=True,
            ),
            ToolSpec(
                "web",
                "Fetch a URL and return readable text. action=fetch. Search requires an external provider and is not enabled in M0.",
                {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["fetch", "search"]},
                        "url": {"type": "string"},
                        "query": {"type": "string"},
                    },
                    "required": ["action"],
                },
                self._web,
                read_only=True,
            ),
            ToolSpec(
                "lsp",
                "Language-server operations. Not available in M0; use search/read/check instead.",
                {"type": "object", "properties": {"method": {"type": "string"}}},
                lambda **_: ToolResult("error", "lsp is not available in this milestone"),
                read_only=True,
            ),
            ToolSpec(
                "subagent",
                "Delegate to a child agent. Not available in M0.",
                {"type": "object", "properties": {"task": {"type": "string"}}},
                lambda **_: ToolResult("error", "subagent is not available in this milestone"),
                read_only=True,
            ),
        ]
        return {spec.name: spec for spec in specs}

    # ------------------------------------------------------------- registry
    def openai_tools(self) -> list[dict[str, Any]]:
        return [spec.openai_schema() for spec in self._specs.values()]

    def call(self, name: str, arguments: dict[str, Any] | str | None = None) -> ToolResult:
        spec = self._specs.get(name)
        if not spec:
            return ToolResult("error", f"unknown tool: {name}")
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                return ToolResult("error", f"invalid JSON arguments: {exc}")
        else:
            args = arguments or {}
        if not isinstance(args, dict):
            return ToolResult("error", "arguments must be an object")
        required = spec.schema.get("required", [])
        missing = [key for key in required if key not in args]
        if missing:
            return ToolResult("error", f"missing required arguments: {', '.join(missing)}")
        denied = self._approval_denied(spec, args)
        if denied is not None:
            return denied
        try:
            return spec.handler(**args)
        except TypeError as exc:
            return ToolResult("error", f"invalid arguments: {exc}")
        except Exception as exc:
            return ToolResult("error", f"{type(exc).__name__}: {exc}")

    # -------------------------------------------------------------- handlers
    def _read(self, path: str, start: int | None = None, end: int | None = None) -> ToolResult:
        result = self.workspace.read(path, start=start, end=end)
        if result.status == "ok":
            self._read_paths.add(self._path_key(path))
        return result

    def _edit(
        self,
        path: str,
        mode: str,
        content: str | None = None,
        old: str | None = None,
        new: str | None = None,
    ) -> ToolResult:
        denied = self._explore_denied("edit")
        if denied is not None:
            return denied
        key = self._path_key(path)
        try:
            exists = self.workspace.resolve(path).exists()
        except (OSError, ValueError) as exc:
            return ToolResult("error", str(exc), {"path": path})
        if exists and mode != "create" and key not in self._read_paths:
            return ToolResult(
                "error",
                f"read {path} before editing it (the file exists and has not been read)",
                {"path": path},
            )
        return self.workspace.edit(path, mode=mode, content=content, old=old, new=new)

    def _search(self, mode: str, query: str, glob: str | None = None) -> ToolResult:
        return self.workspace.search(mode=mode, query=query, glob=glob)

    def _shell(self, command: str, cwd: str | None = None, timeout: int = 120) -> ToolResult:
        denied = self._explore_denied("shell")
        if denied is not None:
            return denied
        return self.workspace.shell(command, cwd=cwd, timeout=timeout)

    def _git(self, args: str) -> ToolResult:
        if self.permission_mode == "explore":
            first = (args.strip().split() or [""])[0]
            if first not in {"status", "diff", "log", "show", "rev-parse"}:
                return ToolResult(
                    "error",
                    f"git {first or 'operation'} is disabled in explore mode",
                )
        return self.workspace.git(args)

    def _check(self, kind: str = "test", command: str | None = None) -> ToolResult:
        denied = self._explore_denied("check")
        if denied is not None:
            return denied
        return self.workspace.check(kind=kind, command=command)

    def _knowledge(
        self,
        action: str,
        query: str | None = None,
        id: str | None = None,
        title: str | None = None,
        body: str | None = None,
        kind: str | None = None,
        scope: str | None = None,
        source: str | None = None,
        outcome: str | None = None,
        validation: str | None = None,
        limit: int = 8,
    ) -> ToolResult:
        if self.permission_mode == "explore" and action in {
            "write",
            "validate",
            "feedback",
        }:
            return ToolResult("error", f"knowledge {action} is disabled in explore mode")
        if action == "search":
            hits = self.knowledge.search(
                query or "",
                scope=scope,
                kinds=[kind] if kind else None,
                limit=limit,
            )
            return ToolResult(
                "ok",
                f"found {len(hits)} knowledge items",
                {
                    "hits": [
                        {
                            "id": hit.item.id,
                            "kind": hit.item.kind,
                            "scope": hit.item.scope,
                            "title": hit.item.title,
                            "snippet": hit.snippet,
                            "confidence": hit.item.confidence,
                        }
                        for hit in hits
                    ]
                },
            )
        if action == "get":
            if not id:
                return ToolResult("error", "id is required for get")
            item = self.knowledge.get(id)
            if not item:
                return ToolResult("error", f"knowledge not found: {id}")
            return ToolResult("ok", item.title, {"item": asdict(item)})
        if action == "write":
            if not title or not body:
                return ToolResult("error", "title and body are required for write")
            item_kind = kind or "fact"
            item = KnowledgeItem(
                title=title,
                body=body,
                kind=item_kind,
                scope=scope or "project",
                # Agent-originated semantic memory starts as a candidate.
                # Commands/docs/contracts are evidence-like and can be active.
                status=(
                    "candidate"
                    if item_kind in {"lesson", "preference", "decision", "assumption", "fact"}
                    else "active"
                ),
                project_id=self.knowledge.project_id,
                trust="derived",
                source=SourceRef(kind="session", ref=source or self.session_id),
            )
            item_id = self.knowledge.write(item, actor="agent")
            return ToolResult("ok", f"wrote knowledge {item_id}", {"id": item_id})
        if action == "validate":
            if not id:
                return ToolResult("error", "id is required for validate")
            if not validation:
                return ToolResult(
                    "error",
                    "validation evidence is required to promote a candidate (replay/test result)",
                )
            evidence = validation.strip()
            lower = evidence.lower()
            if lower.startswith("replay:"):
                method = "replay"
            elif lower.startswith("test:"):
                method = "test"
            else:
                return ToolResult(
                    "error",
                    "the agent must provide replay:/test: evidence; "
                    "manual promotion is a human/CLI action",
                )
            ok = self.knowledge.promote(
                id,
                {"method": method, "result": "passed", "note": evidence},
                actor="agent",
            )
            return ToolResult("ok" if ok else "error", "knowledge promoted" if ok else "not found")
        if action == "feedback":
            if not id or not outcome:
                return ToolResult("error", "id and outcome are required for feedback")
            ok = self.knowledge.feedback(id, outcome)
            return ToolResult("ok" if ok else "error", "feedback recorded" if ok else "not found")
        return ToolResult("error", f"unsupported knowledge action: {action}")

    def _plan(self, action: str, content: str | None = None) -> ToolResult:
        if action == "read":
            if not self.plan_path.exists():
                return ToolResult("error", "no plan for this session")
            return ToolResult(
                "ok", "current plan", {"content": self.plan_path.read_text(encoding="utf-8")}
            )
        if action == "write":
            if not content:
                return ToolResult("error", "content is required for plan write")
            self.plan_path.parent.mkdir(parents=True, exist_ok=True)
            self.plan_path.write_text(content, encoding="utf-8")
            return ToolResult(
                "ok", f"plan written to {self.plan_path}", {"path": str(self.plan_path)}
            )
        if action == "exit":
            if not self.plan_path.exists():
                return ToolResult("error", "no plan to exit with")
            return ToolResult(
                "ok",
                "plan ready for approval",
                {
                    "path": str(self.plan_path),
                    "content": self.plan_path.read_text(encoding="utf-8"),
                },
            )
        return ToolResult("error", f"unsupported plan action: {action}")

    def _ask(self, question: str, options: list[str], recommended: str | None = None) -> ToolResult:
        if self.ask_user_cb:
            answer = self.ask_user_cb(question, options, recommended)
            return ToolResult("ok", f"user answered: {answer}", {"answer": answer})
        return ToolResult(
            "error",
            "interactive input is not available; provide options in the task or use the CLI",
            {"question": question, "options": options, "recommended": recommended},
        )

    def _web(self, action: str, url: str | None = None, query: str | None = None) -> ToolResult:
        if action == "search":
            return ToolResult(
                "error",
                "web search is not configured in M0; use web fetch with a known URL",
                {"query": query},
            )
        if action != "fetch" or not url:
            return ToolResult("error", "url is required for web fetch")
        if not url.startswith(("http://", "https://")):
            return ToolResult("error", "only http(s) URLs are allowed")
        if not is_public_http_url(url):
            return ToolResult(
                "error",
                "refusing to fetch a private/loopback/link-local URL",
                {"url": url},
            )
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "atlas-agent/0.1 (+local)"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read(200_000).decode("utf-8", errors="replace")
        except Exception as exc:
            return ToolResult("error", f"fetch failed: {exc}")
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return ToolResult(
            "ok",
            f"fetched {len(text)} chars from {url} (untrusted web content)",
            {"url": url, "text": text[:20000], "untrusted": True},
        )
