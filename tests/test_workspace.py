from __future__ import annotations

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


def test_workspace_under_build_directory_is_not_skipped(tmp_path: Path) -> None:
    root = tmp_path / "build" / "repo"
    root.mkdir(parents=True)
    (root / "app.py").write_text("hello\n", encoding="utf-8")
    ws = Workspace(root, AtlasConfig(root=root))
    result = ws.search(mode="files", query="*.py")
    assert result.status == "ok"
    assert result.data["matches"] == ["app.py"]
