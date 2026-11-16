from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from atlas.cli import build_parser
from atlas.curator import CuratorJob
from atlas.models import KnowledgeItem
from atlas.orchestrator import Atlas


class FakeModel:
    model = "fake-rca"

    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        return SimpleNamespace(content=self.content, tool_calls=[])


def _write_failure(atlas: Atlas, tmp_path: Path) -> str:
    session = atlas.create_session("failure")
    return atlas.sessions.record_verification(
        session["id"],
        "test",
        "python -m unittest test_calc -q",
        str(tmp_path),
        1,
        "FAILED (failures=1)",
        "AssertionError: 2 != 5",
        120,
    )


def _candidate_for(atlas: Atlas, verification_id: str):
    candidates = atlas.knowledge.list(kind="lesson", status="candidate")
    return next(c for c in candidates if verification_id in (c.source.ref or ""))


def test_curator_retires_expired_items(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="test")
    item_id = atlas.knowledge.write(
        KnowledgeItem(
            title="Expired fact",
            body="This should be retired.",
            kind="fact",
            scope="project",
            expires_at="2000-01-01T00:00:00+00:00",
        )
    )
    result = CuratorJob(atlas).run_once()
    assert result["ok"] is True
    assert result["expired"] >= 1
    item = atlas.knowledge.get(item_id)
    assert item is not None
    assert item.status == "retired"
    assert Path(result["report"]).exists()
    atlas.close()


def test_curator_mines_failed_verification_as_candidate_lesson(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="test")
    session = atlas.create_session("failure")
    verification_id = atlas.sessions.record_verification(
        session["id"],
        "test",
        "python -m unittest test_calc -q",
        str(tmp_path),
        1,
        "FAILED (failures=1)",
        "AssertionError: 2 != 5",
        120,
    )
    result = CuratorJob(atlas).run_once()
    assert result["new_candidates"] >= 1

    candidates = atlas.knowledge.list(kind="lesson", status="candidate")
    assert any(verification_id in (c.source.ref or "") for c in candidates)
    assert atlas.knowledge.search("Failed check") == []

    target = next(c for c in candidates if verification_id in (c.source.ref or ""))
    assert atlas.knowledge.promote(target.id)
    assert atlas.knowledge.search("Failed check")
    atlas.close()


def test_curator_llm_rca_creates_unvalidated_candidate(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="test")
    verification_id = _write_failure(atlas, tmp_path)
    model = FakeModel(
        json.dumps(
            {
                "root_cause": "calc.add subtracts instead of adding.",
                "prevention": "Keep an operator-level regression test and run it after every edit.",
                "confidence": 0.87,
                "evidence": "AssertionError: 2 != 5 from test_calc.",
            }
        )
    )

    result = CuratorJob(atlas, model=model).run_once(use_llm=True)

    assert result["llm_analyzed"] == 1
    assert result["deterministic_fallback"] == 0
    assert result["new_candidates"] >= 1
    assert model.calls == 1

    target = _candidate_for(atlas, verification_id)
    assert "## Root cause" in target.body
    assert "subtracts instead of adding" in target.body
    assert "## Prevention" in target.body
    assert target.validation is not None
    assert target.validation["method"] == "llm-rca"
    assert target.validation["status"] == "unvalidated"
    assert target.status == "candidate"
    assert atlas.knowledge.search("root cause") == []
    atlas.close()


def test_curator_llm_falls_back_on_invalid_json(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="test")
    verification_id = _write_failure(atlas, tmp_path)
    model = FakeModel("I could not produce JSON this time.")

    result = CuratorJob(atlas, model=model).run_once(use_llm=True)

    assert result["llm_analyzed"] == 0
    assert result["deterministic_fallback"] == 1
    assert result["new_candidates"] >= 1
    target = _candidate_for(atlas, verification_id)
    assert target.validation is not None
    assert target.validation["method"] == "deterministic"
    assert target.validation["status"] == "unvalidated"
    assert target.status == "candidate"
    atlas.close()


def test_curator_llm_without_configured_model_is_deterministic(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="test")
    verification_id = _write_failure(atlas, tmp_path)

    result = CuratorJob(atlas).run_once(use_llm=True)

    assert result["llm_analyzed"] == 0
    assert result["deterministic_fallback"] == 1
    target = _candidate_for(atlas, verification_id)
    assert target.validation is not None
    assert target.validation["method"] == "deterministic"
    atlas.close()


def test_curator_cli_accepts_llm_flag() -> None:
    assert build_parser().parse_args(["curator", "run", "--llm"]).llm is True
    assert build_parser().parse_args(["curator", "run"]).llm is False
