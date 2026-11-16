from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from rich.markdown import Markdown

from atlas.llm import ModelReply
from atlas.models import KnowledgeItem
from atlas.orchestrator import Atlas
from atlas.skills import list_skills


pytest.importorskip("textual")

from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Input, Static

from atlas.tui import AtlasTUI, MessageLog, SPINNER_FRAMES


class FakeModel:
    model = "fake"

    def chat(self, messages, tools=None):
        return ModelReply(
            content="hello from fake model",
            tool_calls=[],
            input_tokens=1,
            output_tokens=1,
        )

    def chat_stream(self, messages, tools=None, on_text=None):
        for part in ("hello ", "from fake model"):
            assert on_text is not None
            on_text(part)
        return ModelReply(
            content="hello from fake model",
            tool_calls=[],
            input_tokens=1,
            output_tokens=1,
        )


def test_tui_mounts_and_handles_commands(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-test")
    try:
        atlas.knowledge.write(
            KnowledgeItem(
                title="Project uses pytest",
                body="Run python -m pytest -q.",
                kind="command",
            )
        )
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                assert app.query_one("#prompt")
                app._command("/help")
                app._command("/doctor")
                app._command("/know")
                app._command("/know pytest")
                app._command("/skill list")
                app._command("/knowledge pytest")
                app._command("/bogus")
                old_session = app.session_id
                app._command("/new")
                assert app.session_id != old_session
                await pilot.pause()

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_sends_message_with_fake_model(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-chat-test")
    try:
        session = atlas.create_session("tui")
        atlas._model = FakeModel()
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                prompt = app.query_one("#prompt", Input)
                prompt.value = "hello"
                await pilot.press("enter")
                for _ in range(50):
                    await pilot.pause(0.05)
                    if not app._busy:
                        break
                assert app._busy is False

        asyncio.run(run())
        turns = atlas.sessions.turns(session["id"])
        assert [turn["role"] for turn in turns[-2:]] == ["user", "assistant"]
        assert turns[-1]["content"] == "hello from fake model"
    finally:
        atlas.close()


def test_tui_slash_palette_and_file_palette(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-palette-test")
    try:
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("/")
                await pilot.press("down")
                await pilot.pause()
                assert app._palette.highlighted == 1
                await pilot.press("escape")
                assert app._palette.display is False
                app._prompt.value = ""
                await pilot.press("/")
                await pilot.press("h")
                await pilot.pause()
                assert app._palette.display is True
                assert app._palette.option_count >= 1
                await pilot.press("enter")
                await pilot.pause()
                assert app._palette.display is False
                assert app._prompt.value == ""

                await pilot.press("#")
                await pilot.press(*"pyproject")
                await pilot.pause()
                assert app._palette.display is True
                await pilot.press("enter")
                await pilot.pause()
                assert app._palette.display is False
                assert app._prompt.value.startswith("#pyproject.toml")

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_expands_file_refs(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-file-test")
    try:
        (tmp_path / "notes.txt").write_text("hello attachment\n", encoding="utf-8")
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        expanded, attached = app._expand_file_refs("read #notes.txt")

        assert attached == ["notes.txt"]
        assert "[Attached files]" in expanded
        assert "hello attachment" in expanded
        expanded, attached = app._expand_file_refs("read #missing.txt")
        assert attached == []
        assert "Attached files" not in expanded
    finally:
        atlas.close()


def test_tui_compact_command(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-compact-test")
    try:
        session = atlas.create_session("tui")
        messages = []
        for index in range(20):
            messages.append({"role": "user", "content": f"q{index}"})
            messages.append({"role": "assistant", "content": f"a{index}"})
        atlas.sessions.save_checkpoint(session["id"], None, {"messages": messages})
        atlas._model = FakeModel()
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._command("/compact")
                for _ in range(50):
                    await pilot.pause(0.05)
                    if not app._busy:
                        break
                assert app._busy is False

        asyncio.run(run())
        state = atlas.sessions.load_state(session["id"])
        assert state is not None
        assert len(state["messages"]) < 40
        assert "Conversation summary" in state["messages"][0]["content"]
    finally:
        atlas.close()


def test_tui_skill_install_command(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-skill-test")
    try:
        source = tmp_path / "review.md"
        source.write_text(
            "---\nname: review\ndescription: Review before finishing.\n---\nCheck tests.\n",
            encoding="utf-8",
        )
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._command(f"/skill install {source}")
                for _ in range(50):
                    await pilot.pause(0.05)
                    if not app._busy:
                        break
                assert app._busy is False

        asyncio.run(run())
        assert [skill.name for skill in list_skills(atlas.config.data_dir)] == ["review"]
    finally:
        atlas.close()


def test_tui_tool_blocks_are_collapsible(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-tool-block-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                assert isinstance(app._chat, MessageLog)
                assert isinstance(app._chat, VerticalScroll)
                app._on_agent_event(
                    "tool.call",
                    {
                        "id": "call_1",
                        "name": "read",
                        "arguments": '{"path": "big.txt"}',
                    },
                )
                await pilot.pause()
                blocks = list(app.query(Collapsible))
                assert len(blocks) == 1
                block = blocks[0]
                assert block.collapsed is True
                assert "⏺" not in block.title
                await pilot.pause(0.3)
                assert any(frame in block.title for frame in SPINNER_FRAMES)
                body = block.query_one(".tool-body", Static)
                assert "running" in body.content.plain

                app._on_agent_event(
                    "tool.result",
                    {
                        "id": "call_1",
                        "name": "read",
                        "status": "ok",
                        "summary": "read big.txt",
                        "output": "line\n" * 30,
                    },
                )
                await pilot.pause()
                assert "ok" in block.title
                assert not any(frame in block.title for frame in SPINNER_FRAMES)
                assert "line" in body.content.plain
                block.collapsed = False
                await pilot.pause()
                assert block.collapsed is False

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_palette_height_is_stable_while_filtering(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-layout-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("/")
                await pilot.pause()
                first = app._palette.region.height
                await pilot.press("h")
                await pilot.pause()
                second = app._palette.region.height
                await pilot.press("e")
                await pilot.pause()
                third = app._palette.region.height
                assert first == second == third == 8

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_stream_renders_plain_text_live(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-stream-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._append_stream("**bold** and `code`")
                await pilot.pause()
                assert app._stream.display is True
                # Live streaming shows raw text; half-formed markdown should not
                # be rendered until the reply is complete.
                assert not isinstance(app._stream.content, Markdown)
                assert "**bold**" in app._stream.content.plain
                app._flush_stream()
                await pilot.pause()
                assert app._stream.display is False

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_status_shows_busy_and_idle(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-status-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                idle = app._status.content.plain
                assert "ready" in idle
                app._set_busy(True, "Atlas is thinking...")
                await pilot.pause()
                busy = app._status.content.plain
                assert "thinking" in busy
                assert app._prompt.disabled is True
                app._set_busy(False)
                await pilot.pause()
                assert app._prompt.disabled is False
                assert "ready" in app._status.content.plain

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_session_picker_switches_session(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-session-picker-test")
    try:
        first = atlas.create_session("first")
        current = atlas.create_session("current")
        other = atlas.create_session("other")
        app = AtlasTUI(atlas, current["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._show_sessions()
                await pilot.pause()
                assert app._palette.display is True
                assert app._palette.option_count == 3
                ids = [option.id for option in app._palette.options]
                index = ids.index(f"session:{other['id']}")
                app._palette.highlighted = index
                app._select_palette()
                await pilot.pause()
                assert app.session_id == other["id"]
                assert app._palette.display is False

        asyncio.run(run())
        assert first["id"] != app.session_id
    finally:
        atlas.close()


def test_tui_double_escape_exits(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-escape-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                assert app.is_running
                await pilot.press("escape")
                await pilot.pause()
                assert app.is_running is False

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_status_uses_short_session_id(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-status-short-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                idle = app._status.content.plain
                # The full session id is long and noisy; the status line should
                # show a short prefix instead of the whole thing.
                assert session["id"] not in idle
                assert session["id"][:8] in idle
                assert "ready" in idle

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_stream_grows_with_content(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-stream-grow-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app._append_stream("one line")
                await pilot.pause()
                short = app._stream.region.height
                app._append_stream("\n" + "\n".join(f"line {i}" for i in range(6)))
                await pilot.pause()
                tall = app._stream.region.height
                # The stream area should expand with content instead of being
                # pinned to a fixed 8-row box.
                assert tall > short

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_escape_while_busy_does_not_quit(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-escape-busy-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._set_busy(True, "Atlas is thinking...")
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                # A turn is in flight: quitting would abandon the session
                # mid-write, so the double-Esc must be refused.
                assert app.is_running is True
                assert "Wait for the current turn" in app._status.content.plain

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_escape_after_busy_turn_needs_two_presses(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-escape-reset-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._set_busy(True, "Atlas is thinking...")
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                assert app.is_running is True
                app._set_busy(False)
                await pilot.pause()
                # The refused attempt must not leave a stale timestamp behind:
                # the next single Esc only arms the quit, it does not fire it.
                await pilot.press("escape")
                await pilot.pause()
                assert app.is_running is True
                assert "Press Esc again" in app._status.content.plain
                await pilot.press("escape")
                await pilot.pause()
                assert app.is_running is False

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_escape_after_busy_turn_quits_on_second_press(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-escape-stale-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._set_busy(True, "Atlas is thinking...")
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                assert app.is_running is True
                app._set_busy(False)
                await pilot.pause()
                # The refused attempt must not leave a stale timestamp behind:
                # the next single Esc only arms the quit, it does not fire it.
                await pilot.press("escape")
                await pilot.pause()
                assert app.is_running is True
                assert "Press Esc again" in app._status.content.plain
                await pilot.press("escape")
                await pilot.pause()
                assert app.is_running is False

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_compact_marks_busy_before_task_runs(tmp_path: Path) -> None:
    """`/compact` must lock the prompt synchronously, not when the task starts.

    `asyncio.create_task` only schedules the coroutine; it does not run it
    inline. If `_set_busy(True)` lives inside the coroutine, there is a window
    where `_busy` is still False and a second submit slips through.
    """
    atlas = Atlas.init(tmp_path, name="tui-compact-race-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._command("/compact")
                # No await between the command and this assertion: the task
                # has been scheduled but has not had a chance to run.
                assert app._busy is True
                assert app.query_one("#prompt", Input).disabled is True
                for _ in range(50):
                    await pilot.pause(0.05)
                    if not app._busy:
                        break
                assert app._busy is False

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_skill_install_marks_busy_before_task_runs(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-skill-race-test")
    try:
        source = tmp_path / "review.md"
        source.write_text(
            "---\nname: review\ndescription: Review before finishing.\n---\nCheck tests.\n",
            encoding="utf-8",
        )
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app._command(f"/skill install {source}")
                assert app._busy is True
                assert app.query_one("#prompt", Input).disabled is True
                for _ in range(50):
                    await pilot.pause(0.05)
                    if not app._busy:
                        break
                assert app._busy is False

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_chat_turn_restores_state_via_set_busy(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-busy-test")
    try:
        session = atlas.create_session("tui")
        atlas._model = FakeModel()
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                calls: list[bool] = []
                original = app._set_busy

                def spy(busy: bool, message: str | None = None) -> None:
                    calls.append(busy)
                    original(busy, message)

                app._set_busy = spy  # type: ignore[method-assign]
                prompt = app.query_one("#prompt", Input)
                prompt.value = "hello"
                await pilot.press("enter")
                for _ in range(50):
                    await pilot.pause(0.05)
                    if not app._busy:
                        break
                # The chat path must go through _set_busy for both edges so the
                # busy flag, prompt lock and status line have one owner.
                assert calls == [True, False]
                assert app._busy is False
                assert prompt.disabled is False
                assert "ready" in app._status.content.plain

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_workspace_files_refresh_after_new_file(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-files-refresh-test")
    try:
        (tmp_path / "alpha.py").write_text("print('a')\n", encoding="utf-8")
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                first = app._workspace_files()
                assert "alpha.py" in first
                # A file created after the cache window must show up on the
                # next scan; the listing must not be pinned for the session.
                (tmp_path / "beta.py").write_text("print('b')\n", encoding="utf-8")
                app._files_at = 0.0  # simulate the TTL having elapsed
                second = app._workspace_files()
                assert "beta.py" in second

        asyncio.run(run())
    finally:
        atlas.close()


def test_tui_tool_blocks_do_not_leak_without_result(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="tui-tool-leak-test")
    try:
        session = atlas.create_session("tui")
        app = AtlasTUI(atlas, session["id"])

        async def run() -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                # A tool.call whose result never arrives (interrupted turn)
                # must not accumulate forever in the tracking dict.
                for i in range(60):
                    app._on_agent_event(
                        "tool.call",
                        {"id": f"call_{i}", "name": "read", "arguments": "{}"},
                    )
                await pilot.pause()
                assert len(app._tool_blocks) <= 50

        asyncio.run(run())
    finally:
        atlas.close()
