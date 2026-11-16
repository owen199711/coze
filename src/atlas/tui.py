from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.markdown import Markdown
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Collapsible, Input, Markdown as MarkdownWidget, OptionList, Static
from textual.widgets.option_list import Option

from .config import ConfigError
from .llm import ModelError
from .orchestrator import Atlas
from .skills import install_skill, list_skills, remove_skill


COMMANDS = [
    ("help", "show commands"),
    ("compact", "summarize old context"),
    ("new", "start a new session"),
    ("sessions", "list recent sessions"),
    ("know", "list or search project memory"),
    ("skill", "list/install/remove skills"),
    ("doctor", "show Atlas doctor JSON"),
    ("knowledge", "search project knowledge"),
    ("clear", "clear the screen"),
    ("exit", "quit"),
]
SKIP_DIRS = {
    ".git",
    ".atlas",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    "dist",
    "build",
}
# Seconds a workspace file listing stays cached. Short enough that files
# created mid-session appear, long enough to avoid an os.walk per keystroke.
FILE_CACHE_TTL = 2.0
# Cap on tracked tool blocks so an interrupted turn (tool.call with no
# matching tool.result) cannot grow the dict without bound.
MAX_TOOL_BLOCKS = 50


def is_available() -> bool:
    try:
        import textual  # noqa: F401
        return True
    except Exception:
        return False


class MessageLog(VerticalScroll):
    """Scrollable chat transcript with mountable collapsible tool blocks."""

    def write(self, content: Any) -> Static | None:
        if isinstance(content, Text) and not content.plain.strip():
            return None
        widget = Static(content, classes="message")
        self.mount(widget)
        self.call_after_refresh(self.scroll_end, animate=False)
        return widget

    def write_tool(self, title: str, body: str = "running...") -> tuple[Collapsible, Static]:
        body_widget = Static(body, classes="tool-body")
        block = Collapsible(
            body_widget,
            title=title,
            collapsed=True,
            classes="tool-block",
        )
        self.mount(block)
        self.call_after_refresh(self.scroll_end, animate=False)
        return block, body_widget

    def write_markdown(self, text: str, classes: str = "message") -> None:
        widget = MarkdownWidget(text, classes=classes)
        self.mount(widget)
        self.call_after_refresh(self.scroll_end, animate=False)

    def write_user(self, content: str) -> None:
        stamp = datetime.now().strftime("%H:%M")
        label = Static(
            Text.assemble(("You", "bold #79c0ff"), (f"   {stamp}", "#4b5059")),
            classes="msg-label",
        )
        body = Static(
            Text.assemble(("❯ ", "#d97757"), content),
            classes="msg-user",
        )
        self.mount(label, body)
        self.call_after_refresh(self.scroll_end, animate=False)

    def write_assistant(self, content: str) -> None:
        stamp = datetime.now().strftime("%H:%M")
        label = Static(
            Text.assemble(
                ("✳ Atlas", "bold #d97757"),
                (f"   {stamp}", "#4b5059"),
            ),
            classes="msg-label",
        )
        body = MarkdownWidget(content, classes="msg-assistant")
        self.mount(label, body)
        self.call_after_refresh(self.scroll_end, animate=False)

    def clear(self) -> None:
        self.remove_children()


class AtlasTUI(App[None]):
    """A small opencode-style terminal client.

    It is deliberately a frontend only: all model/tool/session work stays in
    `Atlas`. The CLI falls back to `atlas chat` when Textual is unavailable or
    stdin/stdout is not a TTY.
    """

    TITLE = "Atlas"
    CSS = """
    Screen {
        background: #0d1117;
        color: #e6edf3;
    }
    #topbar {
        height: 1;
        padding: 0 3;
        color: #8b949e;
    }
    #main {
        height: 1fr;
        padding: 1 3;
    }
    #chat {
        height: 1fr;
        background: #0d1117;
        padding: 0 2;
    }
    #stream {
        display: none;
        height: auto;
        min-height: 3;
        max-height: 16;
        overflow-y: auto;
        margin-top: 1;
        padding: 1 2;
        background: #161b22;
        color: #c9d1d9;
    }
    #composer {
        height: 3;
        margin-top: 1;
        padding: 0 2;
        border: round #30363d;
        background: #161b22;
        align: center middle;
    }
    #composer:focus-within {
        border: round #d97757;
    }
    #prompt-sign {
        width: 2;
        color: #d97757;
        content-align: center middle;
    }
    #prompt {
        width: 1fr;
        height: 1;
        border: none;
        background: transparent;
        padding: 0;
    }
    #enter-key {
        width: 7;
        height: 1;
        content-align: center middle;
        color: #6e7681;
        background: #0d1117;
    }
    #palette {
        display: none;
        height: 8;
        overflow-y: auto;
        margin-top: 1;
        padding: 0 1;
        border: round #30363d;
        background: #161b22;
    }
    #status {
        height: 1;
        margin-top: 1;
        color: #6e7681;
        padding: 0 3;
    }
    .msg-label {
        height: 1;
        margin-top: 1;
        padding: 0 1;
    }
    .msg-user {
        height: auto;
        margin-bottom: 1;
        padding: 0 2;
    }
    .msg-assistant {
        height: auto;
        margin-bottom: 1;
        padding: 0 2;
    }
    .message {
        height: auto;
        margin-bottom: 1;
    }
    .tool-block {
        height: auto;
        margin-bottom: 1;
        padding: 0 1;
        background: #161b22;
    }
    .tool-body {
        max-height: 20;
        overflow-y: auto;
        color: #8b949e;
    }
    """
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+n", "new_session", "New session"),
        ("f1", "help", "Help"),
    ]

    def __init__(self, atlas: Atlas, session_id: str | None = None):
        super().__init__()
        self.atlas = atlas
        self.session_id = session_id or atlas.create_session("TUI session")["id"]
        if not atlas.session(self.session_id):
            raise ValueError(f"session not found in this project: {self.session_id}")
        self._busy = False
        self._topbar: Static
        self._chat: MessageLog
        self._stream: Static
        self._prompt: Input
        self._palette: OptionList
        self._status: Static
        self._palette_mode: str | None = None
        self._files: list[str] | None = None
        self._files_at: float = 0.0
        self._last_escape: float = 0.0
        self._stream_buffer = ""
        self._tool_blocks: dict[str, tuple[Collapsible, Static]] = {}

    def compose(self) -> ComposeResult:
        yield Static("", id="topbar")
        with Vertical(id="main"):
            yield MessageLog(id="chat")
            yield Static("", id="stream")
            yield OptionList(id="palette", markup=False)
            with Horizontal(id="composer"):
                yield Static("❯", id="prompt-sign")
                yield Input(
                    placeholder="Ask Atlas...",
                    id="prompt",
                )
                yield Static("Enter", id="enter-key")
            yield Static("", id="status")

    def on_mount(self) -> None:
        self._topbar = self.query_one("#topbar", Static)
        self._chat = self.query_one("#chat", MessageLog)
        self._stream = self.query_one("#stream", Static)
        self._prompt = self.query_one("#prompt", Input)
        self._palette = self.query_one("#palette", OptionList)
        self._status = self.query_one("#status", Static)
        self._palette.display = False
        self._stream.display = False
        self._update_header()
        self._welcome()
        self._prompt.focus()

    def _update_header(self) -> None:
        self._topbar.update(
            Text.assemble(
                ("Atlas", "bold #d97757"),
                ("   ", ""),
                (self.atlas.config.name, "#8b949e"),
                ("  ·  ", "#30363d"),
                (self._model_name(), "#8b949e"),
                ("  ·  ", "#30363d"),
                (self.session_id[:8], "#8b949e"),
            )
        )

    def _model_name(self) -> str:
        return self.atlas.config.model_name or "(not configured)"

    def _status_text(self) -> Text:
        short_session = self.session_id[:8]
        return Text.assemble(
            (self._model_name(), "#8b949e"),
            ("  ·  ", "#30363d"),
            (short_session, "#8b949e"),
            ("  ·  ", "#30363d"),
            ("● ready", "#7ee787"),
            ("   / commands   # files", "#6e7681"),
        )

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        """Single place that flips the busy flag, prompt lock and status line."""
        self._busy = busy
        self._prompt.disabled = busy
        if busy:
            self._status.update(Text(message or "Working...", style="#d29922"))
        else:
            self._status.update(self._status_text())
            self._prompt.focus()

    def _welcome(self) -> None:
        body = Text()
        body.append("Atlas", style="bold #d97757")
        body.append("  lean coding agent\n", style="dim")
        body.append(f"  {self.atlas.config.root}\n\n", style="#8b949e")
        body.append(
            "  / commands   # files   /compact   /know   /skill\n",
            style="#6e7681",
        )
        body.append("  Esc Esc to quit\n", style="#6e7681")
        self._chat.write(body)
        self._status.update(self._status_text())

    def _help(self) -> None:
        self._chat.write_markdown(
            "### Commands\n\n"
            "| command | meaning |\n"
            "|---|---|\n"
            "| `/help` | show this help |\n"
            "| `/compact` | summarize old context |\n"
            "| `/new` | start a new session |\n"
            "| `/sessions` | list recent sessions |\n"
            "| `/know [query]` | list or search project memory |\n"
            "| `/skill list` | list installed skills |\n"
            "| `/skill install <path-or-url>` | install a skill |\n"
            "| `/skill remove <name>` | remove a skill |\n"
            "| `/doctor` | show Atlas doctor JSON |\n"
            "| `/knowledge <query>` | search project knowledge |\n"
            "| `/clear` | clear the screen |\n"
            "| `/exit` | quit |\n\n"
            "Type `/` to pick a command, `#` to pick a workspace file.\n\n"
            "`Ctrl+N` new session · `Ctrl+Q` quit · `F1` help · `Esc Esc` quit"
        )

    def _hide_palette(self) -> None:
        self._palette_mode = None
        self._palette.display = False

    def _workspace_files(self) -> list[str]:
        # The palette rescans on every keystroke while a `#` token is typed, so
        # a full os.walk per keystroke is too costly. Cache briefly instead of
        # forever: a file created mid-session must still show up.
        now = time.monotonic()
        if self._files is not None and now - self._files_at < FILE_CACHE_TTL:
            return self._files
        root = Path(self.atlas.config.root)
        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
            for filename in filenames:
                path = Path(dirpath) / filename
                try:
                    relative = path.relative_to(root).as_posix()
                except ValueError:
                    continue
                found.append(relative)
                if len(found) >= 2000:
                    break
            if len(found) >= 2000:
                break
        self._files = sorted(found)
        self._files_at = now
        return self._files

    def _show_palette(self, mode: str, options: list[Option]) -> None:
        self._palette_mode = mode
        self._palette.clear_options()
        if not options:
            self._hide_palette()
            return
        self._palette.add_options(options)
        self._palette.highlighted = 0
        self._palette.display = True

    def _show_commands(self, query: str) -> None:
        query = query.lower()
        options = [
            Option(f"/{name}  {description}", id=f"cmd:{name}")
            for name, description in COMMANDS
            if query in name.lower() or query in description.lower()
        ]
        self._show_palette("command", options)

    def _show_files(self, query: str) -> None:
        query = query.lower().replace("\\", "/")
        matches = [
            path for path in self._workspace_files() if query in path.lower()
        ][:40]
        options = [Option(path, id=f"file:{path}") for path in matches]
        self._show_palette("file", options)

    def _expand_file_refs(self, text: str) -> tuple[str, list[str]]:
        """Expand `#path` tokens into a bounded, read-only file attachment block."""
        root = Path(self.atlas.config.root).resolve()
        blocks: list[str] = []
        attached: list[str] = []
        total = 0
        for match in re.finditer(r"(?<!\S)#([^\s#]+)", text):
            relative = match.group(1).strip("\"'")
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if not candidate.is_file():
                continue
            raw = candidate.read_bytes()[:20000]
            if b"\x00" in raw:
                continue
            content = raw.decode("utf-8", errors="replace")
            if total + len(content) > 40000:
                break
            blocks.append(f"--- {relative} ---\n{content}")
            attached.append(relative)
            total += len(content)
        if not blocks:
            return text, []
        return text + "\n\n[Attached files]\n" + "\n\n".join(blocks), attached

    def _select_palette(self) -> None:
        option = self._palette.highlighted_option
        if option is None or option.id is None:
            self._hide_palette()
            return
        if self._palette_mode == "command":
            command = option.id.removeprefix("cmd:")
            self._prompt.value = ""
            self._hide_palette()
            self._command(f"/{command}")
            self._prompt.focus()
            return
        if self._palette_mode == "file":
            path = option.id.removeprefix("file:")
            text = self._prompt.value
            index = text.rfind("#")
            prefix = text[:index] if index >= 0 else ""
            self._prompt.value = f"{prefix}#{path} "
            self._hide_palette()
            self._prompt.focus()
            return
        self._hide_palette()

    def on_input_changed(self, event: Input.Changed) -> None:
        text = event.value
        if text.startswith("/") and " " not in text:
            self._show_commands(text[1:])
            return
        hash_index = text.rfind("#")
        if hash_index >= 0 and (hash_index == 0 or text[hash_index - 1].isspace()):
            query = text[hash_index + 1 :]
            if " " not in query:
                self._show_files(query)
                return
        self._hide_palette()

    def on_key(self, event: events.Key) -> None:
        if event.key in {"up", "down"} and self._palette.display:
            if event.key == "up":
                self._palette.action_cursor_up()
            else:
                self._palette.action_cursor_down()
            event.stop()
            event.prevent_default()
            return
        if event.key == "escape":
            if self._palette.display:
                self._hide_palette()
                self._last_escape = 0.0
                event.stop()
                event.prevent_default()
                return
            now = time.monotonic()
            if now - self._last_escape <= 1.5:
                if self._busy:
                    self._status.update(
                        Text("Wait for the current turn to finish.", style="#d29922")
                    )
                    self._last_escape = 0.0
                    event.stop()
                    event.prevent_default()
                    return
                self.exit()
            else:
                self._last_escape = now
                self._status.update(Text("Press Esc again to quit", style="#d29922"))
            event.stop()
            event.prevent_default()

    @on(OptionList.OptionSelected)
    def _on_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._select_palette()
        event.stop()

    def _new_session(self) -> None:
        if self._busy:
            self._chat.write(Text("Wait for the current turn to finish.", style="yellow"))
            return
        session = self.atlas.create_session("TUI session")
        self.session_id = session["id"]
        self._update_header()
        self._tool_blocks.clear()
        self._chat.clear()
        self._welcome()

    def _command(self, text: str) -> None:
        command, _, argument = text.partition(" ")
        command = command.lower()
        if command in {"/exit", "/quit"}:
            if self._busy:
                self._chat.write(Text("Wait for the current turn to finish.", style="yellow"))
                return
            self.exit()
            return
        if command == "/help":
            self._help()
            return
        if command == "/new":
            self._new_session()
            return
        if command == "/clear":
            self._tool_blocks.clear()
            self._chat.clear()
            self._welcome()
            return
        if command == "/sessions":
            rows = self.atlas.list_sessions(limit=20)
            if not rows:
                self._chat.write(Text("No sessions yet.", style="dim"))
            for row in rows:
                marker = " *" if row["id"] == self.session_id else "  "
                self._chat.write(
                    Text(
                        f"{marker} {row['id']}  {row.get('title') or ''}  "
                        f"{row.get('updated_at') or ''}",
                        style="dim",
                    )
                )
            return
        if command == "/compact":
            if self._busy:
                self._chat.write(Text("Wait for the current turn to finish.", style="yellow"))
                return
            # Lock the prompt synchronously: create_task only schedules the
            # coroutine, so setting busy inside it would leave a window where a
            # second submit slips through.
            self._set_busy(True, "Compacting context...")
            asyncio.create_task(self._compact())
            return
        if command == "/know":
            query = argument.strip()
            if query:
                hits = self.atlas.knowledge_search(query, limit=10)
                if not hits:
                    self._chat.write(Text("No memory hits.", style="dim"))
                    return
                for hit in hits:
                    self._chat.write(
                        Text(
                            f"• [{hit.item.kind}] {hit.item.title}\n  {hit.snippet}",
                            style="#c9d1d9",
                        )
                    )
                return
            items = self.atlas.knowledge.list(limit=20)
            if not items:
                self._chat.write(Text("Memory is empty.", style="dim"))
                return
            self._chat.write(Text(f"Memory ({len(items)} active items)", style="bold #d97757"))
            for item in items:
                self._chat.write(
                    Text(
                        f"• [{item.kind}] {item.title}\n  {item.body[:220]}",
                        style="#c9d1d9",
                    )
                )
            return
        if command == "/skill":
            action, _, value = argument.partition(" ")
            action = action.strip().lower()
            if action in {"", "list"}:
                skills = list_skills(self.atlas.config.data_dir)
                if not skills:
                    self._chat.write(Text("No skills installed.", style="dim"))
                    return
                for skill in skills:
                    self._chat.write(
                        Text(
                            f"• {skill.name}  {skill.description}",
                            style="#c9d1d9",
                        )
                    )
                return
            if action == "install":
                source = value.strip()
                if not source:
                    self._chat.write(
                        Text("usage: /skill install <path-or-url>", style="yellow")
                    )
                    return
                if self._busy:
                    self._chat.write(Text("Wait for the current turn to finish.", style="yellow"))
                    return
                self._set_busy(True, "Installing skill...")
                asyncio.create_task(self._install_skill(source))
                return
            if action == "remove":
                name = value.strip()
                if not name or not remove_skill(self.atlas.config.data_dir, name):
                    self._chat.write(Text(f"skill not found: {name}", style="yellow"))
                    return
                self._chat.write(Text(f"removed skill: {name}", style="#7ee787"))
                return
            self._chat.write(
                Text(
                    "usage: /skill list | /skill install <path-or-url> | /skill remove <name>",
                    style="yellow",
                )
            )
            return
        if command == "/doctor":
            payload = json.dumps(self.atlas.doctor(), indent=2, ensure_ascii=False)
            self._chat.write(Text(payload, style="dim"))
            return
        if command == "/knowledge":
            query = argument.strip()
            if not query:
                self._chat.write(Text("usage: /knowledge <query>", style="yellow"))
                return
            hits = self.atlas.knowledge_search(query, limit=8)
            if not hits:
                self._chat.write(Text("No knowledge hits.", style="dim"))
                return
            for hit in hits:
                self._chat.write(
                    Text(
                        f"[{hit.item.kind}] {hit.item.title}\n{hit.snippet}",
                        style="#c9d1d9",
                    )
                )
            return
        self._chat.write(
            Text(f"unknown command: {command} (try /help)", style="yellow")
        )

    def _append_stream(self, text: str) -> None:
        self._stream_buffer += text
        self._stream.display = True
        lines = self._stream_buffer.count("\n") + 1
        # Grow with the stream, but keep a small floor and a hard cap so a
        # single long answer cannot consume the whole chat area.
        self._stream.styles.height = min(16, max(3, lines))
        # Render raw text while streaming: half-formed markdown would flicker,
        # and Static+Markdown does not honour height:auto. The finished reply is
        # rendered as Markdown in _flush_stream / _finish_stream.
        self._stream.update(Text(self._stream_buffer))

    def _flush_stream(self) -> None:
        if self._stream_buffer.strip():
            self._chat.write_markdown(self._stream_buffer)
        self._stream_buffer = ""
        self._stream.display = False

    def _finish_stream(self, reply: str) -> None:
        content = self._stream_buffer.strip() or reply.strip()
        self._stream_buffer = ""
        self._stream.display = False
        if not content:
            self._chat.write(Text("(empty reply)", style="dim"))
            return
        self._chat.write_assistant(content)

    def _on_agent_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "tool.call":
            self._flush_stream()
            call_id = str(payload.get("id") or payload.get("name") or "tool")
            name = payload.get("name") or "?"
            arguments = str(payload.get("arguments") or "")[:160]
            block, body = self._chat.write_tool(f"⏺ {name}  {arguments}")
            body.update(Text("running...", style="#d29922"))
            self._tool_blocks[call_id] = (block, body)
            # An interrupted turn can leave a tool.call with no matching
            # tool.result; evict the oldest tracked block so the dict stays
            # bounded. The widget itself remains in the transcript.
            while len(self._tool_blocks) > MAX_TOOL_BLOCKS:
                self._tool_blocks.pop(next(iter(self._tool_blocks)))
        elif event_type == "tool.result":
            call_id = str(payload.get("id") or payload.get("name") or "tool")
            name = payload.get("name") or "?"
            status = payload.get("status") or "?"
            summary = str(payload.get("summary") or "")[:120]
            pair = self._tool_blocks.pop(call_id, None)
            if pair is not None:
                block, body = pair
                block.title = f"⏺ {name}  {status}  {summary}"
                body.update(Text(str(payload.get("output") or summary), style="#8b949e"))

    async def _compact(self) -> None:
        try:
            result = await asyncio.to_thread(self.atlas.compact, self.session_id)
        except (ConfigError, ModelError, ValueError, OSError) as exc:
            self._chat.write(Text(f"compact error: {exc}", style="bold #f85149"))
        else:
            if result.get("compacted"):
                self._chat.write(
                    Text(
                        f"compacted {result['before']} → {result['after']} messages",
                        style="#d29922",
                    )
                )
                self._chat.write(Text(str(result.get("summary") or ""), style="dim"))
            else:
                self._chat.write(Text("Context is already short; nothing to compact.", style="dim"))
        finally:
            self._set_busy(False)

    async def _install_skill(self, source: str) -> None:
        try:
            skill = await asyncio.to_thread(
                install_skill, self.atlas.config.data_dir, source
            )
        except (OSError, ValueError) as exc:
            self._chat.write(Text(f"skill install error: {exc}", style="bold #f85149"))
        else:
            self._chat.write(Text(f"installed skill: {skill.name}", style="#7ee787"))
            self._chat.write(
                Text("It will be included in the next system prompt.", style="dim")
            )
        finally:
            self._set_busy(False)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._palette.display:
            self._select_palette()
            return
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self._command(text)
            return
        if self._busy:
            self._chat.write(Text("Atlas is still working...", style="yellow"))
            return

        expanded, attached = self._expand_file_refs(text)
        if attached:
            self._chat.write(Text(f"attached: {', '.join(attached)}", style="dim"))
        self._set_busy(True, "Atlas is thinking...")
        self._stream_buffer = ""
        self._stream.display = False
        self._chat.write_user(text)

        def on_text(delta: str) -> None:
            try:
                self.call_from_thread(self._append_stream, delta)
            except Exception:
                pass

        def on_event(event_type: str, payload: dict[str, Any]) -> None:
            try:
                self.call_from_thread(self._on_agent_event, event_type, payload)
            except Exception:
                pass

        try:
            reply = await asyncio.to_thread(
                self.atlas.chat,
                self.session_id,
                expanded,
                None,
                on_text=on_text,
                on_event=on_event,
            )
        except (ConfigError, ModelError, ValueError, OSError) as exc:
            self._stream.display = False
            self._stream_buffer = ""
            self._chat.write(Text(f"error: {exc}", style="bold #f85149"))
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            self._stream.display = False
            self._stream_buffer = ""
            self._chat.write(
                Text(f"unexpected error: {type(exc).__name__}: {exc}", style="bold #f85149")
            )
        else:
            self._finish_stream(reply)
        finally:
            self._set_busy(False)

    def action_quit(self) -> None:
        if self._busy:
            self._chat.write(Text("Wait for the current turn to finish.", style="yellow"))
            return
        self.exit()

    def action_new_session(self) -> None:
        self._new_session()

    def action_help(self) -> None:
        self._help()


def run_tui(path: str = ".", session_id: str | None = None) -> int:
    atlas = Atlas(path)
    try:
        AtlasTUI(atlas, session_id).run()
    finally:
        atlas.close()
    return 0
