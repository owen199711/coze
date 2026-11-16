from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .config import AtlasConfig
from .knowledge import KnowledgeService
from .llm import ModelError, OpenAICompatibleClient
from .models import ToolResult
from .sessions import SessionStore
from .skills import skills_prompt
from .tools import AtlasTools
from .workspace import Workspace


def build_system_prompt(config: AtlasConfig, knowledge: KnowledgeService) -> str:
    agents_md = ""
    agents_path = config.root / "AGENTS.md"
    if agents_path.exists():
        agents_md = agents_path.read_text(encoding="utf-8")[:6000]

    knowledge_lines = []
    for item in knowledge.list(limit=12):
        knowledge_lines.append(f"- [{item.kind}] {item.title}: {item.body[:180]}")
    knowledge_block = "\n".join(knowledge_lines) if knowledge_lines else "- (empty)"
    skills_block = skills_prompt(config.data_dir)

    return f"""You are Atlas, a coding agent working inside a real repository.

## Project
- name: {config.name}
- root: {config.root}
- test command: {config.test_command}
- permission mode: {config.permission_mode}

## Rules
- Read a file before editing it.
- Prefer small, test-first changes.
- Use `check` before claiming a change is verified; report the command and exit code.
- Do not guess API behaviour; use `web`/`read`/`search` and cite the source.
- Do not commit secrets, force-push, or edit `main` directly.
- When uncertain about user intent, use `ask_user` with 2-4 options and a recommended default.
- Use `knowledge` to recall project facts/preferences; write only durable, sourced items.

## AGENTS.md
{agents_md or "(none)"}

## Project knowledge (top items)
{knowledge_block}

{skills_block}
"""


def _trim_history(messages: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    """Keep the recent tail without starting with an orphan tool result."""
    history = list(messages[-limit:])
    while history and history[0].get("role") == "tool":
        history.pop(0)
    return history


def _emit(
    callback: Callable[[str, dict[str, Any]], None] | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        callback(event_type, payload)
    except Exception:
        # A UI callback must never break the agent loop.
        pass


def _looks_like_tool_markup(text: str) -> bool:
    """Detect provider special tokens that leaked into plain content."""
    return "DSML" in text or ("<|" in text and "|>" in text)


class AgentRuntime:
    def __init__(
        self,
        config: AtlasConfig,
        workspace: Workspace,
        knowledge: KnowledgeService,
        sessions: SessionStore,
        model: OpenAICompatibleClient | None = None,
        ask_user=None,
    ):
        self.config = config
        self.workspace = workspace
        self.knowledge = knowledge
        self.sessions = sessions
        self.model = model
        self.ask_user = ask_user

    def run(
        self,
        session_id: str,
        user_message: str,
        max_turns: int | None = None,
        *,
        on_text: Callable[[str], None] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> str:
        if self.model is None:
            raise ModelError(
                "No model configured. Set model.name/api_key_env in atlas.yaml, "
                "or run `atlas demo` for the offline end-to-end demo."
            )
        state = self.sessions.load_state(session_id) or {"messages": []}
        messages: list[dict[str, Any]] = list(state.get("messages") or [])
        messages.append({"role": "user", "content": user_message})
        turn_id = self.sessions.append_turn(session_id, "user", user_message)
        self.sessions.event(session_id, turn_id, "turn.started", {"message": user_message})
        # Save immediately: a model/network failure must not lose the user turn.
        self.sessions.save_checkpoint(
            session_id,
            turn_id,
            {"messages": messages, "turn_id": turn_id},
            git_sha=self.workspace.git_sha(),
        )

        tools = AtlasTools(
            workspace=self.workspace,
            knowledge=self.knowledge,
            config=self.config,
            session_id=session_id,
            ask_user=self.ask_user,
        )
        system = build_system_prompt(self.config, self.knowledge)
        limit = max_turns or self.config.max_turns
        final_text = ""
        recent_calls: dict[tuple[str, str], int] = {}
        reminded: set[tuple[str, str]] = set()

        for step in range(limit):
            request_messages = [
                {"role": "system", "content": system},
                *_trim_history(messages),
            ]
            tool_schemas = tools.openai_tools()
            if on_text is not None and hasattr(self.model, "chat_stream"):
                reply = self.model.chat_stream(  # type: ignore[union-attr]
                    request_messages,
                    tools=tool_schemas,
                    on_text=on_text,
                )
            else:
                reply = self.model.chat(request_messages, tools=tool_schemas)  # type: ignore[union-attr]
                if on_text is not None and reply.content:
                    on_text(reply.content)
            self.sessions.record_usage(
                session_id,
                self.model.model,
                reply.input_tokens,
                reply.output_tokens,
                0.0,
            )
            self.sessions.event(
                session_id,
                turn_id,
                "model.reply",
                {
                    "step": step,
                    "content": reply.content[:2000],
                    "tool_calls": len(reply.tool_calls),
                    "input_tokens": reply.input_tokens,
                    "output_tokens": reply.output_tokens,
                },
            )
            _emit(
                on_event,
                "model.reply",
                {
                    "step": step,
                    "content": reply.content[:2000],
                    "tool_calls": len(reply.tool_calls),
                },
            )

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": reply.content or "",
            }
            if reply.tool_calls:
                assistant_message["tool_calls"] = reply.tool_calls
            messages.append(assistant_message)

            if not reply.tool_calls:
                final_text = reply.content or ""
                break

            reminders: list[str] = []
            for call in reply.tool_calls:
                function = (call.get("function") or {})
                name = function.get("name") or ""
                arguments = function.get("arguments") or "{}"
                self.sessions.event(
                    session_id,
                    turn_id,
                    "tool.call",
                    {"id": call.get("id") or name, "name": name, "arguments": arguments[:2000]},
                )
                _emit(
                    on_event,
                    "tool.call",
                    {
                        "id": call.get("id") or name,
                        "name": name,
                        "arguments": arguments[:2000],
                    },
                )
                key = (name, arguments)
                seen = recent_calls.get(key, 0) + 1
                recent_calls[key] = seen
                if seen >= 3:
                    result = ToolResult(
                        "error",
                        "duplicate tool call suppressed; use the previous result and answer now",
                    )
                    self.sessions.event(
                        session_id,
                        turn_id,
                        "tool.duplicate",
                        {"name": name, "arguments": arguments[:2000], "count": seen},
                    )
                else:
                    result = tools.call(name, arguments)
                self.sessions.event(
                    session_id,
                    turn_id,
                    "tool.result",
                    {
                        "id": call.get("id") or name,
                        "name": name,
                        "status": result.status,
                        "summary": result.summary,
                        "data_keys": list(result.data.keys()),
                    },
                )
                _emit(
                    on_event,
                    "tool.result",
                    {
                        "id": call.get("id") or name,
                        "name": name,
                        "status": result.status,
                        "summary": result.summary,
                        "output": result.as_text(3000),
                    },
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or name,
                        "name": name,
                        "content": result.as_text(),
                    }
                )
                if seen == 2 and key not in reminded:
                    reminded.add(key)
                    reminders.append(
                        f"You already called `{name}` with the same arguments. "
                        "Use the result above and answer the user's request now; "
                        "do not repeat the same tool call."
                    )
            for reminder in reminders:
                messages.append({"role": "system", "content": reminder})
            reminders.clear()
            # Save only after every tool_call in this batch has a result.
            # A partial checkpoint would contain N tool_calls with < N tool
            # messages, which is invalid input for the next model request.
            self.sessions.save_checkpoint(
                session_id,
                turn_id,
                {"messages": messages, "turn_id": turn_id},
                git_sha=self.workspace.git_sha(),
            )
        else:
            final_text = self._finalize(messages, session_id, turn_id)

        self.sessions.append_turn(session_id, "assistant", final_text)
        self.sessions.save_checkpoint(
            session_id,
            turn_id,
            {"messages": messages, "turn_id": turn_id},
            git_sha=self.workspace.git_sha(),
        )
        self.sessions.event(
            session_id, turn_id, "turn.finished", {"content": final_text[:2000]}
        )
        _emit(on_event, "turn.finished", {"content": final_text[:2000]})
        return final_text

    def _finalize(self, messages: list[dict[str, Any]], session_id: str, turn_id: str) -> str:
        """One no-tools synthesis call after the tool budget runs out."""
        fallback = (
            "I stopped after the maximum number of tool rounds. "
            "Review the partial work and ask me to continue."
        )
        request = [
            {
                "role": "system",
                "content": (
                    "The tool budget is exhausted. Do not call tools. Use the "
                    "evidence already gathered and answer the user's request now. "
                    "Return plain prose only; never emit tool-call markup or "
                    "special tokens. If something is still missing, say exactly "
                    "what is missing."
                ),
            },
            *_trim_history(messages),
        ]
        try:
            reply = self.model.chat(request)  # type: ignore[union-attr]
        except Exception as exc:
            self.sessions.event(
                session_id,
                turn_id,
                "model.finalize_error",
                {"error": f"{type(exc).__name__}: {exc}"[:1000]},
            )
            return fallback
        self.sessions.record_usage(
            session_id,
            self.model.model,  # type: ignore[union-attr]
            reply.input_tokens,
            reply.output_tokens,
            0.0,
        )
        self.sessions.event(
            session_id,
            turn_id,
            "model.finalize",
            {
                "content": (reply.content or "")[:2000],
                "input_tokens": reply.input_tokens,
                "output_tokens": reply.output_tokens,
            },
        )
        content = (reply.content or "").strip()
        if _looks_like_tool_markup(content):
            self.sessions.event(
                session_id,
                turn_id,
                "model.finalize_rejected",
                {"reason": "tool-call markup leaked into final text"},
            )
            return fallback
        return content or fallback

    def compact(self, session_id: str, keep_recent: int = 8) -> dict[str, Any]:
        """Summarize older context and keep a short recent tail in the checkpoint."""
        if self.model is None:
            raise ModelError(
                "No model configured. Set model.name/api_key_env in atlas.yaml."
            )
        state = self.sessions.load_state(session_id) or {"messages": []}
        messages = list(state.get("messages") or [])
        if len(messages) <= keep_recent:
            return {
                "ok": True,
                "compacted": False,
                "before": len(messages),
                "after": len(messages),
            }
        older = messages[:-keep_recent]
        recent = messages[-keep_recent:]
        lines: list[str] = []
        for message in older:
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")
            if role == "assistant" and message.get("tool_calls"):
                names = [
                    str((call.get("function") or {}).get("name") or "?")
                    for call in message.get("tool_calls") or []
                ]
                lines.append(f"assistant requested tools: {', '.join(names)}")
            elif role == "tool":
                lines.append(f"tool result: {content[:1200]}")
            else:
                lines.append(f"{role}: {content[:2000]}")
        transcript = "\n\n".join(lines)[:16000]
        reply = self.model.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are compacting a coding-agent conversation. Preserve "
                        "the user's goal, decisions, files touched, commands/results, "
                        "failures, and open questions. Be concise. Return only the summary."
                    ),
                },
                {"role": "user", "content": transcript},
            ]
        )
        summary = (reply.content or "").strip()
        if not summary:
            raise ModelError("compaction returned an empty summary")
        new_messages = [
            {"role": "system", "content": f"Conversation summary (compacted):\n{summary}"},
            *recent,
        ]
        self.sessions.save_checkpoint(
            session_id,
            None,
            {"messages": new_messages, "compacted": True, "summary": summary},
            git_sha=self.workspace.git_sha(),
        )
        self.sessions.record_usage(
            session_id,
            self.model.model,
            reply.input_tokens,
            reply.output_tokens,
            0.0,
        )
        self.sessions.event(
            session_id,
            None,
            "context.compacted",
            {
                "before": len(messages),
                "after": len(new_messages),
                "summary_chars": len(summary),
            },
        )
        return {
            "ok": True,
            "compacted": True,
            "before": len(messages),
            "after": len(new_messages),
            "summary": summary,
        }
