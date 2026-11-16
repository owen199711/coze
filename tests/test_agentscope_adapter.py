from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from atlas.orchestrator import Atlas
from atlas.tools import AtlasTools


def test_agentscope_adapter_builds_agent(tmp_path: Path) -> None:
    pytest.importorskip("agentscope")
    from agentscope.credential import DashScopeCredential
    from agentscope.model import DashScopeChatModel

    from atlas.agentscope_adapter import build_agent

    atlas = Atlas.init(tmp_path, name="agentscope-test")
    session = atlas.create_session("agentscope")
    tools = AtlasTools(atlas.workspace, atlas.knowledge, atlas.config, session["id"])
    model = DashScopeChatModel(
        credential=DashScopeCredential(api_key="sk-test"),
        model="qwen-plus",
        stream=False,
    )
    agent = build_agent(model, tools, atlas.config, session["id"])
    assert type(agent).__name__ == "Agent"
    atlas.close()


def test_agentscope_adapter_restores_text_turns(tmp_path: Path) -> None:
    pytest.importorskip("agentscope")
    from agentscope.credential import DashScopeCredential
    from agentscope.model import DashScopeChatModel

    from atlas.agentscope_adapter import build_agent

    atlas = Atlas.init(tmp_path, name="agentscope-restore")
    try:
        session = atlas.create_session("agentscope")
        atlas.sessions.append_turn(session["id"], "user", "first question")
        atlas.sessions.append_turn(session["id"], "assistant", "first answer")
        tools = AtlasTools(atlas.workspace, atlas.knowledge, atlas.config, session["id"])
        model = DashScopeChatModel(
            credential=DashScopeCredential(api_key="sk-test"),
            model="qwen-plus",
            stream=False,
        )

        agent = build_agent(
            model,
            tools,
            atlas.config,
            session["id"],
            sessions=atlas.sessions,
        )

        assert [msg.get_text_content() for msg in agent.state.context] == [
            "first question",
            "first answer",
        ]
    finally:
        atlas.close()


def test_agentscope_adapter_runs_tool_roundtrip(tmp_path: Path) -> None:
    pytest.importorskip("agentscope")
    from agentscope.credential import DashScopeCredential
    from agentscope.formatter import DashScopeChatFormatter
    from agentscope.message import TextBlock, ToolCallBlock, ToolResultBlock
    from agentscope.model import ChatModelBase, ChatResponse, FinishedReason

    from atlas.agentscope_adapter import build_agent, run_once

    class FakeChatModel(ChatModelBase):
        def __init__(self) -> None:
            super().__init__(
                credential=DashScopeCredential(api_key="sk-test"),
                model="fake",
                parameters=ChatModelBase.Parameters(),
                stream=False,
            )
            self.formatter = DashScopeChatFormatter()
            self.calls = 0
            self.tool_output = ""

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            self.calls += 1
            results = [
                block
                for message in messages
                for block in message.get_content_blocks()
                if isinstance(block, ToolResultBlock)
            ]
            if not results:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_1",
                            name="read",
                            input=json.dumps({"path": "AGENTS.md"}),
                        )
                    ],
                    is_last=True,
                    finished_reason=FinishedReason.COMPLETED,
                )

            output = results[0].output
            if isinstance(output, str):
                self.tool_output = output
            else:
                self.tool_output = "\n".join(
                    getattr(block, "text", "") for block in output
                )
            return ChatResponse(
                content=[TextBlock(text="read ok")],
                is_last=True,
                finished_reason=FinishedReason.COMPLETED,
            )

    (tmp_path / "AGENTS.md").write_text("# Probe rules\nBe careful.\n", encoding="utf-8")
    atlas = Atlas.init(tmp_path, name="agentscope-test")
    try:
        session = atlas.create_session("agentscope")
        tools = AtlasTools(atlas.workspace, atlas.knowledge, atlas.config, session["id"])
        model = FakeChatModel()

        agent = build_agent(model, tools, atlas.config, session["id"])
        schemas = asyncio.run(agent.toolkit.get_tool_schemas())
        assert len(schemas) == 12

        assert (
            run_once(
                model,
                tools,
                atlas.config,
                session["id"],
                "read AGENTS.md",
                sessions=atlas.sessions,
            )
            == "read ok"
        )
        assert model.calls == 2
        assert "Be careful." in model.tool_output
        turns = atlas.sessions.turns(session["id"])
        assert [turn["content"] for turn in turns[-2:]] == ["read AGENTS.md", "read ok"]
    finally:
        atlas.close()


def test_agentscope_adapter_reads_knowledge(tmp_path: Path) -> None:
    pytest.importorskip("agentscope")
    from agentscope.credential import DashScopeCredential
    from agentscope.formatter import DashScopeChatFormatter
    from agentscope.message import TextBlock, ToolCallBlock, ToolResultBlock
    from agentscope.model import ChatModelBase, ChatResponse, FinishedReason

    from atlas.agentscope_adapter import run_once
    from atlas.models import KnowledgeItem

    class FakeChatModel(ChatModelBase):
        def __init__(self) -> None:
            super().__init__(
                credential=DashScopeCredential(api_key="sk-test"),
                model="fake",
                parameters=ChatModelBase.Parameters(),
                stream=False,
            )
            self.formatter = DashScopeChatFormatter()
            self.calls = 0
            self.tool_output = ""

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            self.calls += 1
            results = [
                block
                for message in messages
                for block in message.get_content_blocks()
                if isinstance(block, ToolResultBlock)
            ]
            if not results:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_1",
                            name="knowledge",
                            input=json.dumps(
                                {"action": "search", "query": "pytest", "limit": 3}
                            ),
                        )
                    ],
                    is_last=True,
                    finished_reason=FinishedReason.COMPLETED,
                )

            output = results[0].output
            if isinstance(output, str):
                self.tool_output = output
            else:
                self.tool_output = "\n".join(
                    getattr(block, "text", "") for block in output
                )
            return ChatResponse(
                content=[TextBlock(text="knowledge ok")],
                is_last=True,
                finished_reason=FinishedReason.COMPLETED,
            )

    atlas = Atlas.init(tmp_path, name="agentscope-test")
    try:
        session = atlas.create_session("agentscope")
        tools = AtlasTools(atlas.workspace, atlas.knowledge, atlas.config, session["id"])
        atlas.knowledge.write(
            KnowledgeItem(
                title="Run tests with pytest",
                body="Use `python -m pytest -q` from the repository root.",
                kind="command",
            )
        )
        model = FakeChatModel()

        result = run_once(
            model,
            tools,
            atlas.config,
            session["id"],
            "what test command should I use?",
        )
        assert result == "knowledge ok"
        assert model.calls == 2
        assert "Run tests with pytest" in model.tool_output
    finally:
        atlas.close()

