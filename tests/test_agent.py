from __future__ import annotations

from pathlib import Path

from atlas.llm import ModelReply
from atlas.models import ToolResult
from atlas.orchestrator import Atlas


class ToolLoopModel:
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0
        self.final_calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if tools is None:
            self.final_calls += 1
            return ModelReply(
                content="final summary from evidence",
                tool_calls=[],
                input_tokens=10,
                output_tokens=5,
            )
        return ModelReply(
            content="",
            tool_calls=[
                {
                    "id": f"call_{self.calls}",
                    "type": "function",
                    "function": {
                        "name": "read",
                        "arguments": '{"path": "README.md"}',
                    },
                }
            ],
            input_tokens=5,
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


class BadFinalModel(ToolLoopModel):
    def chat(self, messages, tools=None):
        if tools is None:
            return ModelReply(
                content="<|tool_calls|>DSML call leaked<|/tool_calls|>",
                tool_calls=[],
                input_tokens=1,
                output_tokens=1,
            )
        return super().chat(messages, tools=tools)


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


def test_agent_finalizes_after_tool_budget(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello project\n", encoding="utf-8")
    atlas = Atlas.init(tmp_path, name="agent-finalize-test")
    try:
        atlas.config.max_turns = 3
        model = ToolLoopModel()
        atlas._model = model
        session = atlas.create_session("loop")
        reply = atlas.chat(session["id"], "introduce this project")
        assert reply == "final summary from evidence"
        assert model.final_calls == 1
        events = atlas.sessions.events(session["id"])
        types = [event["type"] for event in events]
        assert "tool.duplicate" in types
        assert "model.finalize" in types
        assert "I stopped after" not in reply
    finally:
        atlas.close()


def test_compact_replaces_old_messages_with_summary(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="agent-compact-test")
    try:
        atlas._model = CompactModel()
        session = atlas.create_session("compact")
        messages = []
        for index in range(20):
            messages.append({"role": "user", "content": f"question {index}"})
            messages.append({"role": "assistant", "content": f"answer {index}"})
        atlas.sessions.save_checkpoint(
            session["id"], None, {"messages": messages}
        )

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
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    atlas = Atlas.init(tmp_path, name="agent-markup-test")
    try:
        atlas.config.max_turns = 1
        atlas._model = BadFinalModel()
        session = atlas.create_session("markup")
        reply = atlas.chat(session["id"], "do something")
        assert "DSML" not in reply
        assert "<|" not in reply
        assert "I stopped after" in reply
        types = [event["type"] for event in atlas.sessions.events(session["id"])]
        assert "model.finalize_rejected" in types
    finally:
        atlas.close()
