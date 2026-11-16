from __future__ import annotations

import fnmatch
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import AtlasConfig
from .models import ToolResult

SKIP_DIRS = {
    ".git",
    ".atlas",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
}
MAX_READ_LINES = 400
MAX_OUTPUT = 12000

DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/(?:\s|$)",
    r"rm\s+-rf\s+~",
    r"git\s+push\s+--force",
    r"git\s+push\s+-f",
    r"git\s+reset\s+--hard",
    r"shutdown\b",
    r"mkfs\b",
    r"format\s+[a-zA-Z]:",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
]


class Workspace:
    def __init__(self, root: str | Path, config: AtlasConfig):
        self.root = Path(root).resolve()
        self.config = config

    # --------------------------------------------------------------- safety
    def resolve(self, path: str | Path) -> Path:
        raw = Path(path)
        target = raw if raw.is_absolute() else self.root / raw
        resolved = target.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path escapes workspace: {path}") from exc
        return resolved

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _should_skip(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self.root)
        except (OSError, ValueError):
            return True
        return any(part in SKIP_DIRS for part in relative.parts)

    # ----------------------------------------------------------------- read
    def read(self, path: str, start: int | None = None, end: int | None = None) -> ToolResult:
        target = self.resolve(path)
        if not target.exists():
            return ToolResult("error", f"file not found: {path}", {"path": path})
        if target.is_dir():
            return ToolResult("error", f"is a directory: {path}", {"path": path})
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult("error", f"binary file: {path}", {"path": path})
        lines = text.splitlines()
        total = len(lines)
        start_idx = max(0, (start or 1) - 1)
        end_idx = min(total, end or total)
        selected = lines[start_idx:end_idx]
        truncated = len(selected) > MAX_READ_LINES
        if truncated:
            selected = selected[:MAX_READ_LINES]
        content = "\n".join(selected)
        return ToolResult(
            "ok",
            f"read {path} lines {start_idx + 1}-{start_idx + len(selected)} of {total}",
            {
                "path": path,
                "start": start_idx + 1,
                "end": start_idx + len(selected),
                "total_lines": total,
                "truncated": truncated,
                "content": content,
            },
        )

    # ----------------------------------------------------------------- edit
    def edit(
        self,
        path: str,
        mode: str = "replace_text",
        content: str | None = None,
        old: str | None = None,
        new: str | None = None,
        create_parents: bool = True,
    ) -> ToolResult:
        target = self.resolve(path)
        if mode == "create":
            if target.exists():
                return ToolResult("error", f"file already exists: {path}")
            if content is None:
                return ToolResult("error", "content is required for create")
            if create_parents:
                target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            self._clear_bytecode(target)
            return ToolResult("ok", f"created {path}", {"path": path, "bytes": len(content)})

        if not target.exists():
            return ToolResult("error", f"file not found: {path}")
        before = target.read_text(encoding="utf-8")

        if mode == "replace":
            if content is None:
                return ToolResult("error", "content is required for replace")
            target.write_text(content, encoding="utf-8")
            self._clear_bytecode(target)
            return ToolResult(
                "ok",
                f"replaced {path}",
                {"path": path, "before_chars": len(before), "after_chars": len(content)},
            )

        if mode == "replace_text":
            if old is None or new is None:
                return ToolResult("error", "old and new are required for replace_text")
            count = before.count(old)
            if count == 0:
                return ToolResult("error", f"old text not found in {path}")
            if count > 1:
                return ToolResult(
                    "error",
                    f"old text appears {count} times; provide a unique snippet",
                    {"path": path, "occurrences": count},
                )
            after = before.replace(old, new, 1)
            target.write_text(after, encoding="utf-8")
            self._clear_bytecode(target)
            return ToolResult("ok", f"edited {path}", {"path": path, "occurrences": 1})

        return ToolResult("error", f"unsupported edit mode: {mode}")

    def _clear_bytecode(self, target: Path) -> None:
        """Avoid stale Python bytecode when a source file is edited twice in one second."""
        if target.suffix == ".py":
            shutil.rmtree(target.parent / "__pycache__", ignore_errors=True)

    # --------------------------------------------------------------- search
    def search(
        self,
        mode: str = "content",
        query: str = "",
        glob: str | None = None,
        max_results: int = 80,
    ) -> ToolResult:
        if mode == "files":
            pattern = query or "*"
            matches: list[str] = []
            for path in self.root.rglob("*"):
                if path.is_dir() or self._should_skip(path):
                    continue
                relative = self.relative(path)
                if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern):
                    matches.append(relative)
                    if len(matches) >= max_results:
                        break
            return ToolResult("ok", f"found {len(matches)} files", {"matches": matches})

        if not query:
            return ToolResult("error", "query is required for content search")
        try:
            regex = re.compile(query, re.IGNORECASE)
        except re.error as exc:
            return ToolResult("error", f"invalid regex: {exc}")
        matches = []
        for path in self.root.rglob("*"):
            if path.is_dir() or self._should_skip(path):
                continue
            relative = self.relative(path)
            if glob and not fnmatch.fnmatch(path.name, glob) and not fnmatch.fnmatch(
                relative, glob
            ):
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(
                        {"path": relative, "line": line_no, "text": line.strip()[:300]}
                    )
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
        return ToolResult("ok", f"found {len(matches)} matches", {"matches": matches})

    # ---------------------------------------------------------------- shell
    def shell(self, command: str, cwd: str | None = None, timeout: int = 120) -> ToolResult:
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return ToolResult("error", f"command denied by policy: {command}")
        workdir = self.resolve(cwd) if cwd else self.root
        started = time.monotonic()
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult("error", f"command timed out after {timeout}s: {command}")
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = (completed.stdout or "")[-MAX_OUTPUT:]
        stderr = (completed.stderr or "")[-MAX_OUTPUT:]
        status = "ok" if completed.returncode == 0 else "error"
        summary = f"exit {completed.returncode} in {duration_ms}ms: {command}"
        return ToolResult(
            status,
            summary,
            {
                "command": command,
                "cwd": str(workdir),
                "exit_code": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "duration_ms": duration_ms,
            },
        )

    # ------------------------------------------------------------------ git
    def git(self, args: str, timeout: int = 60) -> ToolResult:
        try:
            parts = shlex.split(args, posix=(os.name != "nt"))
            if os.name == "nt":
                parts = [part.strip('"') for part in parts]
        except ValueError as exc:
            return ToolResult("error", f"invalid git arguments: {exc}")
        if not parts:
            return ToolResult("error", "git arguments are required")
        allowed = {
            "status",
            "diff",
            "log",
            "branch",
            "commit",
            "show",
            "rev-parse",
            "add",
        }
        if parts[0] not in allowed:
            return ToolResult(
                "error",
                f"git subcommand not allowed: {parts[0]}; allowed: {', '.join(sorted(allowed))}",
            )
        destructive = {
            "--hard",
            "--force",
            "-f",
            "--mirror",
            "--prune",
            "--amend",
            "-D",
            "--delete",
            "--discard-changes",
        }
        if any(part in destructive for part in parts):
            return ToolResult("error", f"destructive git option denied: {args}")
        completed = subprocess.run(
            ["git", *parts],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        status = "ok" if completed.returncode == 0 else "error"
        return ToolResult(
            status,
            f"git {args} exited {completed.returncode}",
            {
                "args": parts,
                "exit_code": completed.returncode,
                "stdout": (completed.stdout or "")[-MAX_OUTPUT:],
                "stderr": (completed.stderr or "")[-MAX_OUTPUT:],
            },
        )

    # ---------------------------------------------------------------- check
    def check(self, kind: str = "test", command: str | None = None) -> ToolResult:
        commands = {
            "test": self.config.test_command,
            "lint": self.config.lint_command,
            "typecheck": self.config.typecheck_command,
            "format": self.config.format_command,
        }
        selected = command or commands.get(kind)
        if not selected:
            return ToolResult("error", f"no command configured for check kind: {kind}")
        result = self.shell(selected, timeout=600)
        result.data["kind"] = kind
        return result

    # ----------------------------------------------------------------- misc
    def git_sha(self) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    def snapshot(self) -> dict[str, Any]:
        files = []
        for path in self.root.rglob("*"):
            if path.is_dir() or self._should_skip(path):
                continue
            try:
                files.append(
                    {"path": self.relative(path), "size": path.stat().st_size}
                )
            except OSError:
                continue
        return {"root": str(self.root), "git_sha": self.git_sha(), "files": files[:2000]}
