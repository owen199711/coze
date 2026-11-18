from __future__ import annotations

from pathlib import Path

import pytest

from atlas.agent import build_system_prompt
from atlas.models import KnowledgeItem
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


def test_system_prompt_requires_chinese_answers(tmp_path: Path) -> None:
    """The constitution must pin the answer language to Chinese."""
    atlas = Atlas.init(tmp_path, name="answer-language")
    try:
        prompt = build_system_prompt(atlas.config, atlas.knowledge)
        assert "中文" in prompt
        assert "Answer in Chinese" in prompt
    finally:
        atlas.close()


def test_system_prompt_states_memory_triggers(tmp_path: Path) -> None:
    """The prompt must say *when* to write knowledge, not just what is allowed.

    Auto memory only works if the model knows the trigger conditions; the
    previous wording ("write only durable, sourced items") constrained the
    content but never named a moment to write.
    """
    atlas = Atlas.init(tmp_path, name="memory-triggers")
    try:
        prompt = build_system_prompt(atlas.config, atlas.knowledge)
        # Highest-priority trigger: the user corrects us or states a preference.
        assert "corrects you" in prompt
        # Dedupe: search before writing so entries are updated, not duplicated.
        assert "search" in prompt.lower()
        # Brevity: few high-value items beat many low-value ones.
        assert "few" in prompt.lower()
    finally:
        atlas.close()


def test_system_prompt_does_not_claim_empty_knowledge(tmp_path: Path) -> None:
    """An empty store must not be rendered as a fake `- (empty)` knowledge item."""
    atlas = Atlas.init(tmp_path, name="empty-knowledge")
    try:
        prompt = build_system_prompt(atlas.config, atlas.knowledge)
        assert "(empty)" not in prompt
        assert "no project knowledge yet" in prompt
    finally:
        atlas.close()


def test_system_prompt_renders_written_knowledge(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="written-knowledge")
    try:
        atlas.knowledge.write(
            KnowledgeItem(
                title="Tests run with pytest",
                body="Use `pytest -q` from the repo root.",
                kind="command",
                scope="project",
                project_id=atlas.knowledge.project_id,
            )
        )
        prompt = build_system_prompt(atlas.config, atlas.knowledge)
        assert "[command] Tests run with pytest" in prompt
        assert "no project knowledge yet" not in prompt
    finally:
        atlas.close()
