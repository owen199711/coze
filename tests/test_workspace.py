from __future__ import annotations

import sys
from pathlib import Path

import pytest

from atlas.config import AtlasConfig
from atlas.workspace import Workspace


@pytest.fixture()
def workspace(tmp_path: Path) -> Workspace:
    config = AtlasConfig(root=tmp_path)
    return Workspace(tmp_path, config)


def test_create_read_edit(workspace: Workspace) -> None:
    created = workspace.edit("hello.py", mode="create", content="x = 1\n")
    assert created.status == "ok"
    read = workspace.read("hello.py")
    assert "x = 1" in read.data["content"]
    edited = workspace.edit("hello.py", mode="replace_text", old="x = 1", new="x = 2")
    assert edited.status == "ok"
    assert "x = 2" in workspace.read("hello.py").data["content"]


def test_read_truncates_by_chars_and_reports_next_start(workspace: Workspace) -> None:
    body = "\n".join(f"line {index:03d} " + "x" * 80 for index in range(1, 201))
    workspace.edit("big.txt", mode="create", content=body)
    result = workspace.read("big.txt", max_chars=2000)
    assert result.status == "ok"
    assert result.data["truncated"] is True
    assert len(result.data["content"]) <= 2000
    assert result.data["content_chars"] == len(result.data["content"])
    assert result.data["next_start"] > 1
    assert result.data["end"] == result.data["next_start"] - 1


def test_read_reports_no_next_start_for_complete_file(workspace: Workspace) -> None:
    workspace.edit("small.txt", mode="create", content="one\ntwo\n")
    result = workspace.read("small.txt", max_chars=2000)
    assert result.status == "ok"
    assert result.data["truncated"] is False
    assert result.data["next_start"] is None


def test_path_escape_denied(workspace: Workspace) -> None:
    with pytest.raises(ValueError):
        workspace.read("../outside.txt")


def test_search_content(workspace: Workspace) -> None:
    workspace.edit("a.py", mode="create", content="def hello():\n    return 1\n")
    result = workspace.search(mode="content", query="hello")
    assert result.status == "ok"
    assert any(m["path"] == "a.py" for m in result.data["matches"])


def test_shell_denies_dangerous(workspace: Workspace) -> None:
    result = workspace.shell("rm -rf /")
    assert result.status == "error"
    assert "denied" in result.summary


def test_search_rejects_unsafe_regex_patterns(workspace: Workspace) -> None:
    nested = workspace.search(mode="content", query="(a+)+")
    assert nested.status == "error"
    assert "nested quantifier" in nested.summary
    too_long = workspace.search(mode="content", query="a" * 201)
    assert too_long.status == "error"
    assert "too long" in too_long.summary


def test_shell_timeout_terminates_process(workspace: Workspace) -> None:
    result = workspace.shell(
        f'"{sys.executable}" -c "import time; time.sleep(10)"',
        timeout=1,
    )
    assert result.status == "error"
    assert "timed out" in result.summary


def test_files_search_applies_glob_filter(workspace: Workspace) -> None:
    workspace.edit("src/app.py", mode="create", content="x = 1\n")
    workspace.edit("docs/app.py", mode="create", content="y = 1\n")
    result = workspace.search(mode="files", query="*.py", glob="src/**")
    assert result.status == "ok"
    assert result.data["matches"] == ["src/app.py"]


def test_workspace_under_build_directory_is_not_skipped(tmp_path: Path) -> None:
    root = tmp_path / "build" / "repo"
    root.mkdir(parents=True)
    (root / "app.py").write_text("hello\n", encoding="utf-8")
    ws = Workspace(root, AtlasConfig(root=root))
    result = ws.search(mode="files", query="*.py")
    assert result.status == "ok"
    assert result.data["matches"] == ["app.py"]
