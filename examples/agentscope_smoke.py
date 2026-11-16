"""Run one Atlas task through AgentScope's Agent (experimental).

The example passes `atlas.sessions` so prior text turns are restored and the
new user/assistant turns are recorded. Requires:
    pip install -e ".[dev]"
    pip install "agentscope>=2.0"

Set DASHSCOPE_API_KEY before running.
"""

from __future__ import annotations

import os

from agentscope.credential import DashScopeCredential
from agentscope.model import DashScopeChatModel

from atlas.agentscope_adapter import run_once
from atlas.orchestrator import Atlas
from atlas.tools import AtlasTools


def main() -> None:
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise SystemExit(
            "DASHSCOPE_API_KEY is not set.\n"
            "Set it first, for example:\n"
            "  PowerShell: $env:DASHSCOPE_API_KEY='sk-...'\n"
            "  bash:       export DASHSCOPE_API_KEY=sk-..."
        )

    atlas = Atlas(".")
    session = atlas.create_session("agentscope smoke")
    tools = AtlasTools(atlas.workspace, atlas.knowledge, atlas.config, session["id"])
    model = DashScopeChatModel(
        credential=DashScopeCredential(api_key=api_key),
        model=atlas.config.model_name or "qwen-plus",
        stream=False,
    )
    try:
        print(
            run_once(
                model=model,
                tools=tools,
                config=atlas.config,
                session_id=session["id"],
                message="Read AGENTS.md and summarize the project rules in three bullets.",
                sessions=atlas.sessions,
            )
        )
    finally:
        atlas.close()


if __name__ == "__main__":
    main()
