from __future__ import annotations

from pathlib import Path

import pytest

from atlas.agent import build_system_prompt
from atlas.db import Database
from atlas.knowledge import KnowledgeService, RecallQuery
from atlas.llm import ModelReply
from atlas.models import KnowledgeItem, SourceRef
from atlas.orchestrator import Atlas
from atlas.tools import AtlasTools


class ReplyModel:
    model = "fake"

    def chat(self, messages, tools=None):
        return ModelReply(
            content="ok",
            tool_calls=[],
            input_tokens=1,
            output_tokens=1,
        )


@pytest.fixture()
def service(tmp_path: Path):
    db = Database(tmp_path / "memory.db")
    db.initialize()
    svc = KnowledgeService(db, "proj_memory")
    try:
        yield svc
    finally:
        db.close()


def _fact(title: str, body: str, **kwargs) -> KnowledgeItem:
    return KnowledgeItem(
        title=title,
        body=body,
        kind="fact",
        scope="project",
        source=SourceRef(kind="file", ref="README.md"),
        **kwargs,
    )


def test_recall_prefers_relevant_memory_over_recent_noise(service: KnowledgeService) -> None:
    relevant = service.write(
        _fact(
            "Relevant old memory",
            "unique-needle: the build uses a custom lock file.",
        )
    )
    service.db.execute(
        "UPDATE knowledge_items SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?",
        (relevant,),
    )
    for index in range(12):
        service.write(_fact(f"Recent noise {index}", f"unrelated noise {index}"))

    hits = service.recall(RecallQuery(text="unique-needle"))
    assert [hit.item.id for hit in hits][:1] == [relevant]
    assert "[kn_" in hits[0].citation
    assert "README.md" in hits[0].citation


def test_prompt_uses_query_aware_recall(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="recall-prompt")
    try:
        relevant = atlas.knowledge.write(
            _fact("Unique build lock", "unique-needle: the build uses a custom lock file.")
        )
        atlas.db.execute(
            "UPDATE knowledge_items SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (relevant,),
        )
        for index in range(12):
            atlas.knowledge.write(_fact(f"Noise {index}", f"unrelated {index}"))
        prompt = build_system_prompt(atlas.config, atlas.knowledge, "unique-needle")
        assert "Unique build lock" in prompt
        assert "unrelated 0" not in prompt
        assert "[kn_" in prompt
    finally:
        atlas.close()


def test_prompt_injects_up_to_twenty_memories(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="recall-limit")
    try:
        for index in range(20):
            atlas.knowledge.write(_fact(f"Memory {index}", f"shared-token body {index}"))
        prompt = build_system_prompt(atlas.config, atlas.knowledge, "shared-token")
        injected = [index for index in range(20) if f"Memory {index}" in prompt]
        assert len(injected) == 20
    finally:
        atlas.close()


def test_estimate_tokens_counts_cjk_as_one_token_per_char() -> None:
    from atlas.knowledge import estimate_tokens

    # 中文：约 1 字 1 token，旧实现 len//4 会严重低估
    assert estimate_tokens("中文记忆内容") == 6
    # 纯 ASCII：保持 4 字符 1 token 的近似
    assert estimate_tokens("abcdefgh") == 2
    # 混合：中文按字计，ASCII 按 4 字符计
    assert estimate_tokens("中文abcd") == 3
    # 空串与空白至少算 1，避免零成本无限注入
    assert estimate_tokens("") == 1
    assert estimate_tokens("   ") == 1


def test_recall_budget_accounts_for_cjk_cost(service: KnowledgeService) -> None:
    # 每条 100 个中文字 ≈ 100 token；预算 250 只应装下 2 条
    for index in range(5):
        service.write(_fact(f"中文记忆 {index}", "记" * 100))
    hits = service.recall(RecallQuery(text="中文记忆", limit=20, budget_tokens=250))
    assert len(hits) == 2


def test_wildcard_search_is_escaped(service: KnowledgeService) -> None:
    service.write(_fact("Wildcard", "ordinary content"))
    assert service.search("%") == []
    assert service.search("_") == []


def test_feedback_does_not_revive_quarantined(service: KnowledgeService) -> None:
    item_id = service.write(_fact("Suspect", "probably wrong", confidence=0.35))
    service.feedback(item_id, "ineffective")
    assert service.get(item_id).status == "quarantined"  # type: ignore[union-attr]
    service.feedback(item_id, "effective")
    assert service.get(item_id).status == "quarantined"  # type: ignore[union-attr]


def test_agent_cannot_self_promote_manual(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="self-promote")
    try:
        item_id = atlas.knowledge.write(
            KnowledgeItem(
                title="Candidate lesson",
                body="Run tests before editing.",
                kind="lesson",
                scope="project",
                status="candidate",
            )
        )
        session = atlas.create_session("tools")
        tools = AtlasTools(atlas.workspace, atlas.knowledge, atlas.config, session["id"])
        denied = tools.call(
            "knowledge",
            {"action": "validate", "id": item_id, "validation": "the model says this is fine"},
        )
        assert denied.status == "error"
        assert atlas.knowledge.get(item_id).status == "candidate"  # type: ignore[union-attr]

        allowed = tools.call(
            "knowledge",
            {"action": "validate", "id": item_id, "validation": "replay: python -m pytest -q"},
        )
        assert allowed.status == "ok"
        assert atlas.knowledge.get(item_id).status == "active"  # type: ignore[union-attr]
    finally:
        atlas.close()


def test_capture_turn_creates_candidate_only(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="capture")
    try:
        atlas._model = ReplyModel()
        session = atlas.create_session("capture")
        atlas.chat(session["id"], "不对，以后都用 2 空格缩进")
        candidates = atlas.knowledge.list(kind="fact", status="candidate")
        assert candidates
        captured = candidates[-1]
        assert "2 空格缩进" in captured.body
        assert captured.source.ref.startswith(session["id"] + ":trn_")
        assert atlas.knowledge.search("2 空格缩进") == []
    finally:
        atlas.close()


def test_capture_ignores_neutral_message(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="capture-neutral")
    try:
        atlas._model = ReplyModel()
        session = atlas.create_session("neutral")
        atlas.chat(session["id"], "explain the project")
        assert atlas.knowledge.list(kind="fact", status="candidate") == []
    finally:
        atlas.close()


def test_learning_disabled_skips_capture(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="capture-disabled")
    try:
        atlas.config.learning_enabled = False
        atlas._model = ReplyModel()
        session = atlas.create_session("disabled")
        atlas.chat(session["id"], "不对，以后都用 2 空格缩进")
        assert atlas.knowledge.list(kind="fact", status="candidate") == []
    finally:
        atlas.close()


def test_agent_knowledge_write_is_candidate(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="agent-write")
    try:
        session = atlas.create_session("tools")
        tools = AtlasTools(atlas.workspace, atlas.knowledge, atlas.config, session["id"])
        for kind in ("fact", "preference", "decision", "assumption"):
            result = tools.call(
                "knowledge",
                {
                    "action": "write",
                    "title": f"Agent {kind}",
                    "body": "body",
                    "kind": kind,
                },
            )
            assert result.status == "ok"
            item = atlas.knowledge.get(result.data["id"])
            assert item is not None
            assert item.status == "candidate"
    finally:
        atlas.close()


def test_search_uses_chunks_for_hybrid_recall(service: KnowledgeService) -> None:
    item_id = service.write(
        _fact(
            "Long guide",
            "intro\n\n"
            + ("filler paragraph. " * 80)
            + "\n\nunique-chunk-needle is in the later chunk.",
        )
    )
    hits = service.search("unique-chunk-needle")
    target = next(hit for hit in hits if hit.item.id == item_id)
    assert target.origin == "hybrid"
    assert target.chunk_id is not None
    assert "unique-chunk-needle" in target.snippet


def test_refresh_sources_marks_changed_doc_stale(
    service: KnowledgeService, tmp_path: Path
) -> None:
    source = tmp_path / "guide.md"
    source.write_text("v1", encoding="utf-8")
    item_id = service.write(
        KnowledgeItem(
            title="Guide",
            body="doc body",
            kind="doc",
            scope="project",
            source=SourceRef(kind="file", ref="guide.md"),
        )
    )
    first = service.refresh_sources(tmp_path)
    assert first == {"checked": 1, "stale": 0}
    source.write_text("v2", encoding="utf-8")
    second = service.refresh_sources(tmp_path)
    assert second == {"checked": 1, "stale": 1}
    assert service.get(item_id).status == "stale"  # type: ignore[union-attr]


def test_agent_cannot_write_active_semantic_memory(service: KnowledgeService) -> None:
    with pytest.raises(ValueError, match="must be candidate"):
        service.write(
            _fact("Active fact", "body"),
            actor="agent",
        )


def test_assumption_gets_default_expiry(service: KnowledgeService) -> None:
    item_id = service.write(
        KnowledgeItem(
            title="Assumption",
            body="The API accepts strings.",
            kind="assumption",
            scope="project",
            status="candidate",
        )
    )
    item = service.get(item_id)
    assert item is not None
    assert item.expires_at is not None


def test_consolidate_stales_old_unused_fact(service: KnowledgeService) -> None:
    item_id = service.write(_fact("Old fact", "Still true?"))
    service.db.execute(
        "UPDATE knowledge_items SET updated_at='2000-01-01T00:00:00+00:00', "
        "last_used_at=NULL, use_count=0 WHERE id=?",
        (item_id,),
    )
    result = service.consolidate()
    assert result["stale_items"] >= 1
    assert service.get(item_id).status == "stale"  # type: ignore[union-attr]


def test_consolidate_stales_unused_lesson(service: KnowledgeService) -> None:
    item_id = service.write(
        KnowledgeItem(
            title="Unused lesson",
            body="A one-off lesson.",
            kind="lesson",
            scope="project",
            status="active",
            validation={"method": "replay", "result": "passed"},
        )
    )
    service.db.execute(
        "UPDATE knowledge_items SET updated_at='2000-01-01T00:00:00+00:00', "
        "last_used_at=NULL, use_count=0 WHERE id=?",
        (item_id,),
    )
    result = service.consolidate()
    assert result["stale_lessons"] >= 1
    assert service.get(item_id).status == "stale"  # type: ignore[union-attr]


def test_write_rolls_back_when_fts_insert_fails(service: KnowledgeService, monkeypatch) -> None:
    assert service._fts is True
    real_execute = service.db.execute

    def failing_execute(sql, params=()):
        if "INSERT INTO knowledge_fts" in sql:
            raise RuntimeError("fts unavailable")
        return real_execute(sql, params)

    monkeypatch.setattr(service.db, "execute", failing_execute)
    with pytest.raises(RuntimeError, match="fts unavailable"):
        service.write(_fact("Must rollback", "body"))
    assert service.list(limit=10) == []
