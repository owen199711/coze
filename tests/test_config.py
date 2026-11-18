from __future__ import annotations

from pathlib import Path

import pytest

from atlas.config import ConfigError, load_config


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


def test_tool_result_max_chars_config(tmp_path: Path) -> None:
    (tmp_path / "atlas.yaml").write_text(
        "version: 1\ntools:\n  result_max_chars: 24000\n",
        encoding="utf-8",
    )
    assert load_config(tmp_path).tool_result_max_chars == 24000
    (tmp_path / "atlas.yaml").write_text(
        "version: 1\ntools:\n  result_max_chars: 100\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="result_max_chars"):
        load_config(tmp_path)
