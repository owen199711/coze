from __future__ import annotations

from pathlib import Path

import pytest

from atlas.agent import build_system_prompt
from atlas.orchestrator import Atlas
from atlas.skills import install_skill, list_skills, remove_skill, skills_prompt


def test_install_list_prompt_and_remove_skill(tmp_path: Path) -> None:
    data_dir = tmp_path / ".atlas"
    source = tmp_path / "web-research.md"
    source.write_text(
        "---\nname: web-research\ndescription: Research a library before coding.\n---\n"
        "# Web research\n\nRead official docs first.\n",
        encoding="utf-8",
    )

    skill = install_skill(data_dir, str(source))

    assert skill.name == "web-research"
    assert skill.path.is_file()
    assert [item.name for item in list_skills(data_dir)] == ["web-research"]
    prompt = skills_prompt(data_dir)
    assert "web-research" in prompt
    assert "Research a library" in prompt
    assert remove_skill(data_dir, "web-research") is True
    assert list_skills(data_dir) == []


def test_missing_skill_source_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="skill not found"):
        install_skill(tmp_path / ".atlas", str(tmp_path / "missing.md"))


def test_system_prompt_includes_installed_skill(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="skill-prompt")
    try:
        source = tmp_path / "review.md"
        source.write_text(
            "---\nname: careful-review\ndescription: Review diffs before finishing.\n---\n"
            "Check tests and edge cases.\n",
            encoding="utf-8",
        )
        install_skill(atlas.config.data_dir, str(source))
        prompt = build_system_prompt(atlas.config, atlas.knowledge)
        assert "Installed skills" in prompt
        assert "careful-review" in prompt
    finally:
        atlas.close()
