from __future__ import annotations

from pathlib import Path

from atlas.config import load_config


def test_project_root_is_resolved_relative_to_config(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "atlas.yaml").write_text(
        "version: 1\nproject:\n  name: demo\n  root: sub\n",
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config.root == sub.resolve()
    assert config.db_path == sub.resolve() / ".atlas" / "atlas.db"
