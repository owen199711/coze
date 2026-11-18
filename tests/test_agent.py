from __future__ import annotations

import sys
from pathlib import Path

import pytest

from atlas.agent import AgentRuntime
from atlas.curator import CuratorJob
from atlas.llm import MaxTurnsExceeded, ModelReply
from atlas.models import ToolResult
from atlas.orchestrator import Atlas


class DistinctToolModel:
    """Calls a different tool each step, so no duplicate guard kicks in."""

    model = "fake"

    def __init__(self, calls: int) -> None:
        self.calls = calls
        self.tool_steps = 0
        self.final_calls = 0

    def chat(self, messages, tools=None):
        if tools is None:
            self.final_calls += 1
            return ModelReply(
                content="final summary from evidence",
                tool_calls=[],
                input_tokens=10,
                output_tokens=5,
            )
        if self.tool_steps >= self.calls:
            return ModelReply(
                content="done after tools",
                tool_calls=[],
                input_tokens=10,
                output_tokens=5,
            )
        self.tool_steps += 1
        return ModelReply(
            content="",
            tool_calls=[
                {
                    "id": f"call_{self.tool_steps}",
                    "type": "function",
                    "function": {
                        "name": "search",
                        "arguments": f'{{"mode": "content", "query": "needle-{self.tool_steps}"}}',
                    },
                }
            ],
            input_tokens=5,
            output_tokens=1,
        )


class MarkupThenProseModel:
    """Leaks tool-call markup on the first replies, then answers in prose."""

    model = "fake"

    def __init__(self) -> None:
        self.final_calls = 0
        self.loop_calls = 0

    def chat(self, messages, tools=None):
        if tools is not None:
            self.loop_calls += 1
            return ModelReply(
                content="<|tool_calls|>DSML call leaked<|/tool_calls|>",
                tool_calls=[],
                input_tokens=1,
                output_tokens=1,
            )
        self.final_calls += 1
        if self.final_calls == 1:
            return ModelReply(
                content="<|tool_calls|>DSML call leaked<|/tool_calls|>",
                tool_calls=[],
                input_tokens=1,
                output_tokens=1,
            )
        return ModelReply(
            content="clean answer after retry",
            tool_calls=[],
            input_tokens=1,
            output_tokens=1,
        )


class CheckModel:
    """Runs one failing check, then answers."""

    model = "fake"

    def __init__(self) -> None:
        self.tool_steps = 0

    def chat(self, messages, tools=None):
        if tools is None or self.tool_steps:
            return ModelReply(
                content="check finished",
                tool_calls=[],
                input_tokens=1,
                output_tokens=1,
            )
        self.tool_steps += 1
        return ModelReply(
            content="",
            tool_calls=[
                {
                    "id": "call_check",
                    "type": "function",
                    "function": {"name": "check", "arguments": '{"kind": "test"}'},
                }
            ],
            input_tokens=1,
            output_tokens=1,
        )


class ExpensiveModel:
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1 and tools is not None:
            return ModelReply(
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path": "README.md"}'},
                    }
                ],
                input_tokens=1_000_000,
                output_tokens=0,
            )
        return ModelReply(
            content="should not be reached",
            tool_calls=[],
            input_tokens=1,
            output_tokens=1,
        )


class CompactModel:
    model = "fake"

    def chat(self, messages, tools=None):
        return ModelReply(
            content="The user wants a compact project introduction.",
            tool_calls=[],
            input_tokens=20,
            output_tokens=10,
        )


class StreamingModel:
    model = "fake"

    def chat(self, messages, tools=None):
        raise AssertionError("streaming path should be used")

    def chat_stream(self, messages, tools=None, on_text=None):
        for part in ("hel", "lo"):
            assert on_text is not None
            on_text(part)
        return ModelReply(
            content="hello",
            tool_calls=[],
            input_tokens=3,
            output_tokens=2,
        )


class BadFinalModel:
    def chat(self, messages, tools=None):
        return ModelReply(
            content="<|tool_calls|>DSML call leaked<|/tool_calls|>",
            tool_calls=[],
            input_tokens=1,
            output_tokens=1,
        )


def test_large_tool_result_uses_larger_budget() -> None:
    result = ToolResult(
        "ok",
        "read big",
        {"path": "big.py", "content": "line\n" * 5000},
    )
    text = result.as_text(16_000)
    assert 4000 < len(text) <= 16_000
    assert '"content_truncated": true' in text


def test_large_tool_result_keeps_content_preview() -> None:
    result = ToolResult(
        "ok",
        "read big",
        {
            "path": "big.py",
            "total_lines": 2000,
            "truncated": False,
            "content": "line\n" * 2000,
        },
    )
    text = result.as_text()
    assert len(text) <= 4000
    assert "data_preview" in text
    assert "line" in text


def test_trim_history_keeps_compaction_summary() -> None:
    from atlas.agent import _trim_history

    messages = [{"role": "system", "content": "summary"}] + [
        {"role": "user", "content": str(index)} for index in range(60)
    ]
    trimmed = _trim_history(messages, limit=40)
    assert trimmed[0] == {"role": "system", "content": "summary"}
    assert len(trimmed) == 41


def test_compact_replaces_old_messages_with_summary(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="agent-compact-test")
    try:
        atlas._model = CompactModel()
        session = atlas.create_session("compact")
        messages = []
        for index in range(20):
            messages.append({"role": "user", "content": f"question {index}"})
            messages.append({"role": "assistant", "content": f"answer {index}"})
        atlas.sessions.save_checkpoint(session["id"], None, {"messages": messages})

        result = atlas.compact(session["id"], keep_recent=4)

        assert result["compacted"] is True
        assert result["before"] == 40
        assert result["after"] == 5
        state = atlas.sessions.load_state(session["id"])
        assert state is not None
        assert state["messages"][0]["role"] == "system"
        assert "Conversation summary" in state["messages"][0]["content"]
        assert len(state["messages"]) == 5
        events = atlas.sessions.events(session["id"])
        assert any(event["type"] == "context.compacted" for event in events)
    finally:
        atlas.close()


def test_streaming_callbacks_are_used(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="agent-stream-test")
    try:
        atlas._model = StreamingModel()
        session = atlas.create_session("stream")
        chunks: list[str] = []
        events: list[str] = []
        reply = atlas.chat(
            session["id"],
            "hello",
            on_text=chunks.append,
            on_event=lambda event_type, _payload: events.append(event_type),
        )
        assert reply == "hello"
        assert chunks == ["hel", "lo"]
        assert "model.reply" in events
        assert "turn.finished" in events
    finally:
        atlas.close()


def test_finalize_rejects_leaked_tool_markup(tmp_path: Path) -> None:
    # ADR-0002: `_finalize` is no longer reached by budget exhaustion, but it
    # still guards the empty-content fallback path against leaked tool markup.
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    atlas = Atlas.init(tmp_path, name="agent-markup-test")
    try:
        runtime = AgentRuntime(
            config=atlas.config,
            workspace=atlas.workspace,
            knowledge=atlas.knowledge,
            sessions=atlas.sessions,
            model=BadFinalModel(),
        )
        session = atlas.create_session("markup")
        turn_id = atlas.sessions.append_turn(session["id"], "user", "do something")
        reply = runtime._finalize(
            [{"role": "user", "content": "do something"}], session["id"], turn_id
        )
        assert "DSML" not in reply
        assert "<|" not in reply
        assert "I could not produce" in reply
        types = [event["type"] for event in atlas.sessions.events(session["id"])]
        assert "model.finalize_rejected" in types
    finally:
        atlas.close()


def test_finalize_retries_once_after_leaked_markup(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    atlas = Atlas.init(tmp_path, name="agent-markup-retry-test")
    try:
        model = MarkupThenProseModel()
        runtime = AgentRuntime(
            config=atlas.config,
            workspace=atlas.workspace,
            knowledge=atlas.knowledge,
            sessions=atlas.sessions,
            model=model,
        )
        session = atlas.create_session("markup-retry")
        turn_id = atlas.sessions.append_turn(session["id"], "user", "do something")
        reply = runtime._finalize(
            [{"role": "user", "content": "do something"}], session["id"], turn_id
        )
        assert reply == "clean answer after retry"
        assert model.final_calls == 2
        types = [event["type"] for event in atlas.sessions.events(session["id"])]
        assert "model.finalize_rejected" in types
        assert "model.finalize_retry" in types
    finally:
        atlas.close()


def test_check_records_verification_and_curator_can_mine_it(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="verification-test")
    try:
        atlas.config.test_command = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
        atlas._model = CheckModel()
        session = atlas.create_session("verification")
        reply = atlas.chat(session["id"], "run the tests")
        assert reply == "check finished"
        rows = atlas.db.fetchall("SELECT * FROM verifications")
        assert len(rows) == 1
        assert rows[0]["exit_code"] == 3
        assert rows[0]["kind"] == "test"

        result = CuratorJob(atlas).run_once()
        assert result["new_candidates"] >= 1
        candidates = atlas.knowledge.list(kind="lesson", status="candidate")
        assert candidates
    finally:
        atlas.close()


def test_max_turns_counts_llm_calls(tmp_path: Path) -> None:
    """ADR-0002: one LLM call is one turn; exceeding the budget raises."""
    (tmp_path / "README.md").write_text("hello project\n", encoding="utf-8")
    atlas = Atlas.init(tmp_path, name="agent-max-turns-test")
    try:
        atlas.config.max_turns = 3
        atlas.config.max_tool_calls = 50
        model = DistinctToolModel(calls=10)
        atlas._model = model
        session = atlas.create_session("max-turns")
        with pytest.raises(MaxTurnsExceeded):
            atlas.chat(session["id"], "keep reading forever")
        assert model.tool_steps == 3
    finally:
        atlas.close()


def test_max_turns_exceeded_carries_messages(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello project\n", encoding="utf-8")
    atlas = Atlas.init(tmp_path, name="agent-max-turns-payload-test")
    try:
        atlas.config.max_turns = 2
        atlas.config.max_tool_calls = 50
        atlas._model = DistinctToolModel(calls=10)
        session = atlas.create_session("max-turns-payload")
        with pytest.raises(MaxTurnsExceeded) as excinfo:
            atlas.chat(session["id"], "keep reading forever")
        error = excinfo.value
        assert error.max_turns == 2
        assert error.messages
        assert any(message.get("role") == "user" for message in error.messages)
    finally:
        atlas.close()


def test_max_turns_exceeded_keeps_partial_work(tmp_path: Path) -> None:
    """A raised MaxTurnsExceeded must not lose the tool evidence gathered."""
    (tmp_path / "README.md").write_text("hello project\n", encoding="utf-8")
    atlas = Atlas.init(tmp_path, name="max-turns-partial-test")
    try:
        atlas.config.max_turns = 2
        atlas.config.max_tool_calls = 50
        atlas._model = DistinctToolModel(calls=10)
        session = atlas.create_session("max-turns-partial")
        with pytest.raises(MaxTurnsExceeded):
            atlas.chat(session["id"], "keep reading forever")

        state = atlas.sessions.load_state(session["id"]) or {}
        saved = state.get("messages") or []
        assert any(message.get("role") == "tool" for message in saved)
        assert any(message.get("role") == "assistant" for message in saved)
        events = [row["type"] for row in atlas.sessions.events(session["id"])]
        assert "turn.max_turns_exceeded" in events
    finally:
        atlas.close()


def test_max_turns_default_is_ten(tmp_path: Path) -> None:
    """ADR-0002: default aligns with openai-agents."""
    atlas = Atlas.init(tmp_path, name="agent-max-turns-default-test")
    try:
        assert atlas.config.max_turns == 10
    finally:
        atlas.close()


def test_max_cost_stops_before_another_model_call(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    atlas = Atlas.init(tmp_path, name="cost-budget-test")
    try:
        atlas.config.input_cost_per_1m = 1.0
        atlas.config.max_cost_usd = 0.5
        model = ExpensiveModel()
        atlas._model = model
        session = atlas.create_session("cost")
        reply = atlas.chat(session["id"], "do it")
        assert model.calls == 1
        assert "cost budget" in reply
        types = [event["type"] for event in atlas.sessions.events(session["id"])]
        assert "turn.cost_exhausted" in types
    finally:
        atlas.close()


def test_first_user_message_becomes_session_title(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="agent-title-test")
    try:
        atlas._model = CompactModel()
        session = atlas.create_session("TUI session")
        atlas.chat(session["id"], "帮我介绍一下这个项目，并说明 TUI 的布局。")
        updated = atlas.session(session["id"])
        assert updated is not None
        assert updated["title"] == "帮我介绍一下这个项目，并说明 TUI 的布局。"
    finally:
        atlas.close()
