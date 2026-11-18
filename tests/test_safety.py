from __future__ import annotations

import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from atlas.config import AtlasConfig
from atlas.orchestrator import Atlas
from atlas.skills import install_skill
from atlas.tools import AtlasTools, _PublicRedirectHandler
from atlas.workspace import Workspace, is_public_http_url


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path, AtlasConfig(root=tmp_path))


def test_sensitive_paths_are_denied(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-secret\n", encoding="utf-8")
    (tmp_path / "key.pem").write_text("private\n", encoding="utf-8")
    (tmp_path / ".atlas" / "skills").mkdir(parents=True)
    (tmp_path / ".atlas" / "skills" / "review.md").write_text("review\n", encoding="utf-8")

    assert workspace.read(".env").status == "error"
    assert workspace.read(".git/config").status == "error"
    assert workspace.read("key.pem").status == "error"
    assert workspace.read(".atlas/skills/review.md").status == "ok"
    assert workspace.edit(".git/hooks/pre-commit", mode="create", content="x").status == "error"
    assert workspace.edit(".env", mode="replace", content="x").status == "error"

    search = workspace.search(mode="files", query="*")
    assert ".env" not in search.data["matches"]
    assert "key.pem" not in search.data["matches"]
    assert ".atlas/skills/review.md" not in search.data["matches"]


def test_edit_preserves_line_endings(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    lf = tmp_path / "lf.py"
    lf.write_bytes(b"a = 1\nb = 2\n")
    assert workspace.edit("lf.py", mode="replace_text", old="b = 2", new="b = 3").status == "ok"
    assert lf.read_bytes() == b"a = 1\nb = 3\n"

    crlf = tmp_path / "crlf.py"
    crlf.write_bytes(b"a = 1\r\nb = 2\r\n")
    assert workspace.edit("crlf.py", mode="replace_text", old="b = 2", new="b = 3").status == "ok"
    assert crlf.read_bytes() == b"a = 1\r\nb = 3\r\n"


def test_read_rejects_out_of_range_start(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (tmp_path / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    result = workspace.read("a.py", start=10)
    assert result.status == "error"
    assert "exceeds" in result.summary


def test_tools_require_read_before_edit(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="read-before-edit")
    try:
        (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
        session = atlas.create_session("tools")
        tools = AtlasTools(atlas.workspace, atlas.knowledge, atlas.config, session["id"])

        denied = tools.call(
            "edit", {"path": "a.py", "mode": "replace_text", "old": "a = 1", "new": "a = 2"}
        )
        assert denied.status == "error"
        assert "read" in denied.summary

        assert tools.call("read", {"path": "a.py"}).status == "ok"
        assert (
            tools.call(
                "edit", {"path": "a.py", "mode": "replace_text", "old": "a = 1", "new": "a = 2"}
            ).status
            == "ok"
        )

        assert (
            tools.call("edit", {"path": "new.py", "mode": "create", "content": "x = 1\n"}).status
            == "ok"
        )
    finally:
        atlas.close()


def test_explore_mode_blocks_writes(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="explore")
    try:
        atlas.config.permission_mode = "explore"
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
        session = atlas.create_session("explore")
        tools = AtlasTools(atlas.workspace, atlas.knowledge, atlas.config, session["id"])

        assert tools.call("read", {"path": "a.py"}).status == "ok"
        assert (
            tools.call(
                "edit", {"path": "a.py", "mode": "replace_text", "old": "a = 1", "new": "a = 2"}
            ).status
            == "error"
        )
        assert tools.call("shell", {"command": "echo hi"}).status == "error"
        assert (
            tools.call(
                "knowledge",
                {"action": "write", "title": "x", "body": "y", "kind": "fact"},
            ).status
            == "error"
        )
        assert tools.call("git", {"args": "status"}).status == "ok"
        assert tools.call("git", {"args": "commit -m x"}).status == "error"
    finally:
        atlas.close()


def test_shell_does_not_expose_secret_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret-value")
    workspace = _workspace(tmp_path)
    code = "import os; print(os.environ.get('DEEPSEEK_API_KEY', 'missing'))"
    result = workspace.shell(f'"{sys.executable}" -c "{code}"')
    assert result.status == "ok"
    assert "sk-secret-value" not in result.data["stdout"]
    assert "missing" in result.data["stdout"]


def test_web_redirect_handler_rejects_private_target() -> None:
    handler = _PublicRedirectHandler()
    request = urllib.request.Request("https://example.com/")
    with pytest.raises(urllib.error.URLError, match="redirect target"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://127.0.0.1:9/",
        )


def test_web_and_skills_reject_private_urls(tmp_path: Path) -> None:
    assert is_public_http_url("http://127.0.0.1:9/") is False
    assert is_public_http_url("http://169.254.169.254/latest/meta-data/") is False
    assert is_public_http_url("file:///etc/passwd") is False

    atlas = Atlas.init(tmp_path, name="ssrf")
    try:
        session = atlas.create_session("ssrf")
        tools = AtlasTools(atlas.workspace, atlas.knowledge, atlas.config, session["id"])
        result = tools.call("web", {"action": "fetch", "url": "http://127.0.0.1:9/"})
        assert result.status == "error"
        assert "private" in result.summary
    finally:
        atlas.close()

    with pytest.raises(ValueError, match="loopback/private"):
        install_skill(tmp_path / ".atlas", "http://127.0.0.1:9/SKILL.md")


def test_tool_schema_rejects_invalid_values(tmp_path: Path) -> None:
    atlas, tools = _tools_with_mode(tmp_path, "accept_edits")
    try:
        assert "invalid arguments" in tools.call(
            "search", {"mode": "bogus", "query": "x"}
        ).summary
        assert "invalid arguments" in tools.call(
            "read", {"path": "a.py", "max_chars": 1}
        ).summary
        assert "invalid arguments" in tools.call(
            "knowledge", {"action": "search", "query": "x", "limit": -1}
        ).summary
    finally:
        atlas.close()


def test_git_refuses_staged_secret_patterns(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=tmp_path, check=True)
    (tmp_path / "leak.py").write_text(
        'TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8"
    )
    assert workspace.git("add leak.py").status == "ok"
    blocked = workspace.git("commit -m leak")
    assert blocked.status == "error"
    assert "secret" in blocked.summary


def test_git_refuses_sensitive_files_and_main_commit(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("ok\n", encoding="utf-8")

    assert workspace.git("add .env").status == "error"
    assert "sensitive" in workspace.git("add .env").summary
    assert workspace.git("add ok.txt").status == "ok"
    blocked = workspace.git("commit -m ok")
    assert blocked.status == "error"
    assert "protected branch" in blocked.summary


def _tools_with_mode(tmp_path: Path, mode: str, ask_user=None) -> tuple[Atlas, AtlasTools]:
    atlas = Atlas.init(tmp_path, name=f"perm-{mode}")
    atlas.config.permission_mode = mode
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    session = atlas.create_session(mode)
    tools = AtlasTools(
        atlas.workspace, atlas.knowledge, atlas.config, session["id"], ask_user=ask_user
    )
    return atlas, tools


def test_default_mode_requires_approval(tmp_path: Path) -> None:
    asked: list[str] = []

    def deny(question: str, options: list[str], recommended: str | None) -> str:
        asked.append(question)
        return "no"

    atlas, tools = _tools_with_mode(tmp_path, "default", ask_user=deny)
    try:
        assert tools.call("read", {"path": "a.py"}).status == "ok"
        result = tools.call(
            "edit", {"path": "a.py", "mode": "replace_text", "old": "a = 1", "new": "a = 2"}
        )
        assert result.status == "error"
        assert asked, "default mode must ask before a write"
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    finally:
        atlas.close()


def test_default_mode_allows_read_only_tool_actions(tmp_path: Path) -> None:
    atlas, tools = _tools_with_mode(tmp_path, "default")
    try:
        search = tools.call("knowledge", {"action": "search", "query": "x"})
        assert "requires approval" not in search.summary

        plan = tools.call("plan", {"action": "read"})
        assert "requires approval" not in plan.summary

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        status = tools.call("git", {"args": "status"})
        assert "requires approval" not in status.summary

        write = tools.call(
            "knowledge",
            {
                "action": "write",
                "title": "x",
                "body": "y",
                "kind": "fact",
            },
        )
        assert "requires approval" in write.summary
    finally:
        atlas.close()


def test_default_mode_without_callback_denies_writes(tmp_path: Path) -> None:
    atlas, tools = _tools_with_mode(tmp_path, "default")
    try:
        result = tools.call(
            "edit", {"path": "a.py", "mode": "replace_text", "old": "a = 1", "new": "a = 2"}
        )
        assert result.status == "error"
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    finally:
        atlas.close()


def test_dont_ask_mode_skips_approval(tmp_path: Path) -> None:
    asked: list[str] = []

    def deny(question: str, options: list[str], recommended: str | None) -> str:
        asked.append(question)
        return "no"

    atlas, tools = _tools_with_mode(tmp_path, "dont_ask", ask_user=deny)
    try:
        assert tools.call("read", {"path": "a.py"}).status == "ok"
        result = tools.call(
            "edit", {"path": "a.py", "mode": "replace_text", "old": "a = 1", "new": "a = 2"}
        )
        assert result.status == "ok"
        assert not asked, "dont_ask must not prompt"
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "a = 2\n"
    finally:
        atlas.close()


def test_accept_edits_mode_skips_approval(tmp_path: Path) -> None:
    asked: list[str] = []

    def deny(question: str, options: list[str], recommended: str | None) -> str:
        asked.append(question)
        return "no"

    atlas, tools = _tools_with_mode(tmp_path, "accept_edits", ask_user=deny)
    try:
        assert tools.call("read", {"path": "a.py"}).status == "ok"
        result = tools.call(
            "edit", {"path": "a.py", "mode": "replace_text", "old": "a = 1", "new": "a = 2"}
        )
        assert result.status == "ok"
        assert not asked, "accept_edits must not prompt"
    finally:
        atlas.close()
