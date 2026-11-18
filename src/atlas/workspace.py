from __future__ import annotations

import fnmatch
import ipaddress
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
MAX_READ_CHARS = 12_000
MAX_READ_BYTES = 2_000_000
MAX_SEARCH_PATTERN = 200
MAX_SEARCH_SECONDS = 5
MAX_OUTPUT = 12000

# Files/directories that must not be exposed to the model even though they live
# inside the workspace. `.atlas/skills` is the one exception: installed skills
# are meant to be read on demand.
SENSITIVE_DIRS = {".git"}
SENSITIVE_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_ed25519",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.*",
    "secrets.*",
)

DANGEROUS_PATTERNS = [
    r"rm\s+-(?:rf|fr)\s+/(?:\s|$|\*)",
    r"rm\s+-r\s+-f\s+/(?:\s|$|\*)",
    r"rm\s+-rf\s+~",
    r"rm\s+-rf\s+\$HOME",
    r"git\s+push\s+--force",
    r"git\s+push\s+-f",
    r"git\s+reset\s+--hard",
    r"git\s+clean\s+-[a-z]*f",
    r"shutdown\b",
    r"reboot\b",
    r"mkfs\b",
    r"format\s+[a-zA-Z]:",
    r"del\s+/[sq]\b",
    r"rd\s+/s\b",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
]


def is_public_http_url(url: str) -> bool:
    """Reject loopback/private/link-local targets before server-side fetches.

    This blocks the common SSRF targets (localhost, RFC1918, cloud metadata,
    `.internal` DNS). DNS rebinding is out of scope for this local MVP, but the
    resolved addresses are checked before the request is made.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except (OSError, socket.gaierror):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            address = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            return False
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
    return True


def is_sensitive_path(
    path: str | Path,
    root: str | Path,
    *,
    allow_skills: bool = False,
) -> bool:
    """True when a path must stay out of model-visible tools.

    The workspace boundary is not enough: `.git` allows hook persistence, and
    `.env`/key files are common secret locations. Installed skills under
    `.atlas/skills` are explicitly readable when `allow_skills` is set.
    """
    root_path = Path(root).resolve()
    try:
        relative = Path(path).resolve().relative_to(root_path)
    except (OSError, ValueError):
        return True
    parts = [part.lower() for part in relative.parts]
    if not parts:
        return False
    if parts[0] == ".git":
        return True
    if parts[0] == ".atlas":
        if allow_skills and len(parts) >= 3 and parts[1] == "skills" and parts[-1].endswith(".md"):
            return False
        return True
    name = parts[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in SENSITIVE_FILE_PATTERNS)


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
    def _read_text(self, target: Path) -> str:
        with target.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()

    def _write_text(self, target: Path, text: str, newline: str = "\n") -> None:
        # Preserve the file's original line endings. Writing with text mode and
        # newline=None would silently turn LF files into CRLF on Windows.
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write(normalized.replace("\n", newline))

    def read(
        self,
        path: str,
        start: int | None = None,
        end: int | None = None,
        max_chars: int | None = None,
    ) -> ToolResult:
        target = self.resolve(path)
        if is_sensitive_path(target, self.root, allow_skills=True):
            return ToolResult("error", f"sensitive path denied: {path}", {"path": path})
        if not target.exists():
            return ToolResult("error", f"file not found: {path}", {"path": path})
        if target.is_dir():
            return ToolResult("error", f"is a directory: {path}", {"path": path})
        try:
            if target.stat().st_size > MAX_READ_BYTES and start is None and end is None:
                return ToolResult(
                    "error",
                    f"file too large to read whole ({target.stat().st_size} bytes); "
                    "specify start/end lines",
                    {"path": path, "size": target.stat().st_size},
                )
            text = self._read_text(target)
        except UnicodeDecodeError:
            return ToolResult("error", f"binary file: {path}", {"path": path})
        lines = text.splitlines()
        total = len(lines)
        start_idx = max(0, (start or 1) - 1)
        if start_idx >= total:
            return ToolResult(
                "error",
                f"start line {start} exceeds file length {total}: {path}",
                {"path": path, "total_lines": total},
            )
        end_idx = min(total, end or total)
        selected = lines[start_idx:end_idx]
        truncated_by_lines = len(selected) > MAX_READ_LINES
        if truncated_by_lines:
            selected = selected[:MAX_READ_LINES]

        char_limit = max(1, max_chars or MAX_READ_CHARS)
        kept: list[str] = []
        used_chars = 0
        truncated_by_chars = False
        partial_line = False
        for line in selected:
            addition = len(line) + (1 if kept else 0)
            if kept and used_chars + addition > char_limit:
                truncated_by_chars = True
                break
            if not kept and len(line) > char_limit:
                kept.append(line[:char_limit])
                used_chars = char_limit
                truncated_by_chars = True
                partial_line = True
                break
            kept.append(line)
            used_chars += addition
        selected = kept
        truncated = truncated_by_lines or truncated_by_chars
        content = "\n".join(selected)
        next_start = start_idx + len(selected) + 1 if truncated else None
        note = f"read {path} lines {start_idx + 1}-{start_idx + len(selected)} of {total}"
        if next_start is not None:
            note += f"; continue at line {next_start}"
        return ToolResult(
            "ok",
            note,
            {
                "path": path,
                "start": start_idx + 1,
                "end": start_idx + len(selected),
                "total_lines": total,
                "truncated": truncated,
                "truncated_by_lines": truncated_by_lines,
                "truncated_by_chars": truncated_by_chars,
                "partial_line": partial_line,
                "content_chars": len(content),
                "next_start": next_start,
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
        if is_sensitive_path(target, self.root):
            return ToolResult("error", f"sensitive path denied: {path}", {"path": path})
        if mode == "create":
            if target.exists():
                return ToolResult("error", f"file already exists: {path}")
            if content is None:
                return ToolResult("error", "content is required for create")
            if create_parents:
                target.parent.mkdir(parents=True, exist_ok=True)
            self._write_text(target, content, "\n")
            self._clear_bytecode(target)
            return ToolResult("ok", f"created {path}", {"path": path, "bytes": len(content)})

        if not target.exists():
            return ToolResult("error", f"file not found: {path}")
        try:
            before = self._read_text(target)
        except UnicodeDecodeError:
            return ToolResult("error", f"binary file: {path}", {"path": path})
        newline = "\r\n" if "\r\n" in before else "\n"
        before_normalized = before.replace("\r\n", "\n")

        if mode == "replace":
            if content is None:
                return ToolResult("error", "content is required for replace")
            self._write_text(target, content, newline)
            self._clear_bytecode(target)
            return ToolResult(
                "ok",
                f"replaced {path}",
                {"path": path, "before_chars": len(before), "after_chars": len(content)},
            )

        if mode == "replace_text":
            if old is None or new is None:
                return ToolResult("error", "old and new are required for replace_text")
            old_normalized = old.replace("\r\n", "\n")
            count = before_normalized.count(old_normalized)
            if count == 0:
                return ToolResult("error", f"old text not found in {path}")
            if count > 1:
                return ToolResult(
                    "error",
                    f"old text appears {count} times; provide a unique snippet",
                    {"path": path, "occurrences": count},
                )
            after = before_normalized.replace(old_normalized, new.replace("\r\n", "\n"), 1)
            self._write_text(target, after, newline)
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
            matches: list[Any] = []
            for path in self.root.rglob("*"):
                if path.is_dir() or self._should_skip(path):
                    continue
                if is_sensitive_path(path, self.root):
                    continue
                relative = self.relative(path)
                if (
                    glob
                    and not fnmatch.fnmatch(path.name, glob)
                    and not fnmatch.fnmatch(relative, glob)
                ):
                    continue
                if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern):
                    matches.append(relative)
                    if len(matches) >= max_results:
                        break
            return ToolResult("ok", f"found {len(matches)} files", {"matches": matches})

        if not query:
            return ToolResult("error", "query is required for content search")
        if len(query) > MAX_SEARCH_PATTERN:
            return ToolResult(
                "error",
                f"search pattern is too long (max {MAX_SEARCH_PATTERN} chars)",
            )
        if re.search(r"\([^()]*[+*][^()]*\)[+*{]", query):
            return ToolResult(
                "error",
                "nested quantifier search patterns are not allowed",
            )
        try:
            regex = re.compile(query, re.IGNORECASE)
        except re.error as exc:
            return ToolResult("error", f"invalid regex: {exc}")
        matches = []
        deadline = time.monotonic() + MAX_SEARCH_SECONDS
        timed_out = False
        for path in self.root.rglob("*"):
            if time.monotonic() > deadline:
                timed_out = True
                break
            if path.is_dir() or self._should_skip(path):
                continue
            if is_sensitive_path(path, self.root):
                continue
            relative = self.relative(path)
            if (
                glob
                and not fnmatch.fnmatch(path.name, glob)
                and not fnmatch.fnmatch(relative, glob)
            ):
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if time.monotonic() > deadline:
                    timed_out = True
                    break
                if regex.search(line):
                    matches.append({"path": relative, "line": line_no, "text": line.strip()[:300]})
                    if len(matches) >= max_results:
                        break
            if timed_out or len(matches) >= max_results:
                break
        data: dict[str, Any] = {"matches": matches}
        if timed_out:
            data["timed_out"] = True
        return ToolResult(
            "ok" if not timed_out else "error",
            (
                f"found {len(matches)} matches"
                if not timed_out
                else f"search timed out after {MAX_SEARCH_SECONDS}s; partial results"
            ),
            data,
        )

    # ---------------------------------------------------------------- shell
    def shell(self, command: str, cwd: str | None = None, timeout: int = 120) -> ToolResult:
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return ToolResult("error", f"command denied by policy: {command}")
        workdir = self.resolve(cwd) if cwd else self.root
        if is_sensitive_path(workdir, self.root):
            return ToolResult("error", f"sensitive cwd denied: {cwd}", {"cwd": cwd})
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            return ToolResult("error", "timeout must be an integer number of seconds")
        if timeout <= 0:
            return ToolResult("error", "timeout must be positive")
        started = time.monotonic()
        # Do not hand API keys/tokens to arbitrary model-directed commands.
        env = {
            key: value
            for key, value in os.environ.items()
            if not re.search(
                r"(?i)(api[_-]?key|token|secret|password|credential|private[_-]?key|access[_-]?key)",
                key,
            )
        }
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        popen_kwargs: dict[str, Any] = {
            "shell": True,
            "cwd": str(workdir),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **popen_kwargs)
        except OSError as exc:
            return ToolResult("error", f"command failed to start: {exc}: {command}")
        try:
            stdout_text, stderr_text = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                killpg = getattr(os, "killpg", None)
                sigkill = getattr(signal, "SIGKILL", None)
                if killpg is not None and sigkill is not None:
                    try:
                        killpg(process.pid, sigkill)
                    except OSError:
                        process.kill()
                else:
                    process.kill()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            return ToolResult("error", f"command timed out after {timeout}s: {command}")
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = (stdout_text or "")[-MAX_OUTPUT:]
        stderr = (stderr_text or "")[-MAX_OUTPUT:]
        status = "ok" if process.returncode == 0 else "error"
        summary = f"exit {process.returncode} in {duration_ms}ms: {command}"
        return ToolResult(
            status,
            summary,
            {
                "command": command,
                "cwd": str(workdir),
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "duration_ms": duration_ms,
            },
        )

    # ------------------------------------------------------------------ git
    def _git_output(self, args: list[str], timeout: int = 30) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return (completed.stdout or "") if completed.returncode == 0 else ""

    def _git_branch(self) -> str:
        branch = self._git_output(["symbolic-ref", "--short", "HEAD"]).strip()
        if branch:
            return branch
        return self._git_output(["rev-parse", "--abbrev-ref", "HEAD"]).strip()

    def _git_paths_to_stage(self, explicit: list[str]) -> list[str]:
        if explicit and not any(item in {".", ":/", "-A", "--all"} for item in explicit):
            return explicit
        names: list[str] = []
        for line in self._git_output(["status", "--porcelain"]).splitlines():
            if len(line) > 3:
                names.append(line[3:].strip().strip('"'))
        return names

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
        if parts[0] == "branch" and any(
            part in {"-d", "-D", "--delete", "-m", "-M", "--move"} for part in parts
        ):
            return ToolResult("error", f"refusing to delete/rename a branch: {args}")

        if parts[0] == "add":
            explicit = [item for item in parts[1:] if not item.startswith("-")]
            for name in self._git_paths_to_stage(explicit):
                if is_sensitive_path(self.root / name, self.root):
                    return ToolResult(
                        "error",
                        f"refusing to stage sensitive file: {name}",
                    )
        if parts[0] == "commit":
            if "--no-verify" in parts or "-n" in parts:
                return ToolResult("error", "refusing to commit with --no-verify")
            branch = self._git_branch()
            if branch in {"main", "master"}:
                return ToolResult(
                    "error",
                    f"refusing to commit directly on protected branch: {branch}",
                )
            staged = [
                name.strip().strip('"')
                for name in self._git_output(["diff", "--cached", "--name-only"]).splitlines()
                if name.strip()
            ]
            for name in staged:
                if is_sensitive_path(self.root / name, self.root):
                    return ToolResult(
                        "error",
                        f"refusing to commit sensitive file: {name}",
                    )
            patch = self._git_output(["diff", "--cached", "--unified=0"])
            from .knowledge import SECRET_PATTERNS

            if any(pattern.search(patch) for pattern in SECRET_PATTERNS):
                return ToolResult("error", "refusing to commit a staged secret")

        completed = subprocess.run(
            ["git", *parts],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
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
            if is_sensitive_path(path, self.root):
                continue
            try:
                files.append({"path": self.relative(path), "size": path.stat().st_size})
            except OSError:
                continue
        return {"root": str(self.root), "git_sha": self.git_sha(), "files": files[:2000]}
