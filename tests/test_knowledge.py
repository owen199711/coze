from __future__ import annotations

from pathlib import Path

import pytest

from atlas.db import Database
from atlas.knowledge import KnowledgeService
from atlas.models import KnowledgeItem, SourceRef


@pytest.fixture()
def service(tmp_path: Path):
    db = Database(tmp_path / "atlas.db")
    db.initialize()
    svc = KnowledgeService(db, "proj_test")
    try:
        yield svc
    finally:
        db.close()


def test_write_search_get_forget(service: KnowledgeService) -> None:
    item_id = service.write(
        KnowledgeItem(
            title="Test command",
            body="Use `python -m pytest -q` from the repository root.",
            kind="command",
            scope="project",
            source=SourceRef(kind="command", ref="atlas demo"),
        )
    )
    hits = service.search("pytest")
    assert hits
    assert hits[0].item.id == item_id
    assert service.get(item_id).title == "Test command"  # type: ignore[union-attr]
    assert service.forget(item_id)
    assert service.get(item_id) is None


def test_dedupe_returns_same_id(service: KnowledgeService) -> None:
    first = service.write(
        KnowledgeItem(title="Style", body="Use 2 spaces.", kind="fact", scope="project")
    )
    second = service.write(
        KnowledgeItem(title="Style", body="Use 2 spaces.", kind="fact", scope="project")
    )
    assert first == second


def test_secret_rejected(service: KnowledgeService) -> None:
    with pytest.raises(ValueError):
        service.write(
            KnowledgeItem(
                title="Token",
                body="secret-value",
                kind="fact",
                scope="project",
                sensitivity="secret",
            )
        )


def test_feedback_quarantines_low_confidence(service: KnowledgeService) -> None:
    item_id = service.write(
        KnowledgeItem(
            title="Wrong fact",
            body="This is wrong.",
            kind="fact",
            scope="project",
            confidence=0.35,
        )
    )
    assert service.feedback(item_id, "ineffective")
    item = service.get(item_id)
    assert item is not None
    assert item.status == "quarantined"


def test_lesson_is_not_recalled_until_validated(service: KnowledgeService) -> None:
    item_id = service.write(
        KnowledgeItem(
            title="Provider JSON mode",
            body="Check the provider docs before using response_format.",
            kind="lesson",
            scope="project",
            status="candidate",
        )
    )
    assert service.search("response_format") == []
    assert service.promote(item_id, {"method": "replay", "result": "passed"})
    hits = service.search("response_format")
    assert hits and hits[0].item.id == item_id


def test_project_isolation(tmp_path: Path) -> None:
    db = Database(tmp_path / "atlas.db")
    db.initialize()
    project_a = KnowledgeService(db, "proj_a")
    project_b = KnowledgeService(db, "proj_b")
    item_id = project_a.write(
        KnowledgeItem(
            title="Secret project fact",
            body="Only project A should see this.",
            kind="fact",
            scope="project",
            project_id="proj_a",
        )
    )
    assert project_a.get(item_id) is not None
    assert project_b.get(item_id) is None
    assert project_b.search("Secret project fact") == []
    assert project_b.forget(item_id) is False
    assert project_a.get(item_id) is not None
    db.close()
