"""Optional AgentScope 2.x adapter.

This module is deliberately isolated from the default M0 path:
- default `atlas chat` uses the small OpenAI-compatible loop;
- this adapter lets you run the same tools/knowledge through AgentScope's
  `Agent` when `agentscope>=2.0` is installed and you provide a model object.

It is still experimental: real DeepSeek `run_once` is validated, and prior user/
assistant text turns can be restored by passing `sessions=atlas.sessions`.
`run_once` also records the new user/assistant turns when `sessions` is supplied.
Tool-call history replay and middleware are still pending. See
examples/agentscope_smoke.py.

`run_once` is a sync convenience wrapper; in async code, call `build_agent` and
`await agent.reply(...)` directly instead.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .agent import build_system_prompt
from .config import AtlasConfig
from .tools import AtlasTools


def is_available() -> bool:
    try:
        import agentscope  # noqa: F401
        return True
    except Exception:
        return False


def _restore_context(sessions: Any, session_id: str, limit: int = 40) -> list[Any]:
    """Rehydrate prior text turns; tool-call history is intentionally not replayed."""
    from agentscope.message import AssistantMsg, UserMsg

    context: list[Any] = []
    for turn in sessions.turns(session_id)[-limit:]:
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        if turn.get("role") == "user":
            context.append(UserMsg(name="user", content=content))
        elif turn.get("role") == "assistant":
            context.append(AssistantMsg(name="atlas", content=content))
    return context


def build_agent(
    model: Any,
    tools: AtlasTools,
    config: AtlasConfig,
    session_id: str,
    sessions: Any | None = None,
) -> Any:
    """Build an AgentScope `Agent` with the same 12 tools and knowledge service.

    When `sessions` is supplied, prior user/assistant text turns are restored
    into `AgentState.context`. Tool calls are not replayed because Atlas keeps
    their evidence in the session journal, not as model context.
    """
    from agentscope.agent import Agent
    from agentscope.message import TextBlock
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.state import AgentState
    from agentscope.tool import ToolBase, ToolChunk, Toolkit

    class _AtlasTool(ToolBase):
        def __init__(self, spec: Any):
            self._spec = spec
            self.name = spec.name
            self.description = spec.description
            self.input_schema = spec.schema
            self.is_read_only = spec.read_only
            self.is_concurrency_safe = False

        async def check_permissions(self, tool_input: dict, context: Any) -> Any:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="Local Atlas tool.",
            )

        async def call(self, **kwargs: Any) -> Any:
            result = tools.call(self._spec.name, kwargs)
            return ToolChunk(content=[TextBlock(text=result.as_text())])

    toolkit = Toolkit(tools=[_AtlasTool(spec) for spec in tools._specs.values()])
    context = _restore_context(sessions, session_id) if sessions is not None else []
    state = AgentState(session_id=session_id, context=context)
    return Agent(
        name="atlas",
        system_prompt=build_system_prompt(config, tools.knowledge),
        model=model,
        toolkit=toolkit,
        state=state,
    )


async def _reply(agent: Any, message: str) -> str:
    from agentscope.message import UserMsg

    result = await agent.reply(UserMsg(name="user", content=message))
    return result.get_text_content() or ""


def run_once(
    model: Any,
    tools: AtlasTools,
    config: AtlasConfig,
    session_id: str,
    message: str,
    sessions: Any | None = None,
) -> str:
    agent = build_agent(model, tools, config, session_id, sessions=sessions)
    if sessions is not None:
        sessions.append_turn(session_id, "user", message)
    result = asyncio.run(_reply(agent, message))
    if sessions is not None:
        sessions.append_turn(session_id, "assistant", result)
    return result
