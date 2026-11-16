# Lean Atlas (Python MVP)

A small, local-first coding agent core built around three ideas:

1. **One knowledge store** for facts, preferences, decisions, lessons, docs and commands.
2. **One session journal** for recovery, checkpoints and audit.
3. **One execution service** for tools, sandboxed commands, tests and evidence.

This is the **M0/M1 Python implementation** of the Lean Atlas v1 spec. It is intentionally
small: no multi-tenant server, no multi-repo, no self-modifying code.

> Full plan: [Lean Atlas v1](docs/lean-atlas-v1.md) · [M0 task board](docs/m0-task-board.md) · [Maturity roadmap](docs/atlas-maturity-roadmap.md)

---

## Quickstart

Requires Python 3.10+.

```bash
pip install -e ".[dev]"
atlas init --path .
atlas doctor
atlas demo
atlas                  # full-screen TUI in a terminal
atlas chat "explain this repo"   # plain one-shot / REPL
atlas tui              # explicit TUI entry point
```

`atlas` is the CLI and does **not** need `atlas serve`. The HTTP daemon is only
for the VS Code extension or another JSON-RPC client.

`atlas demo` runs a complete offline task in a temporary repo:

1. reads a buggy `calc.py`;
2. writes/runs a failing test (red);
3. fixes the bug;
4. runs the test again (green);
5. records verification evidence;
6. writes and searches project knowledge.

No API key is needed for the demo.

---

## Use a real model

Edit `atlas.yaml`:

```yaml
model:
  provider: openai-compatible
  name: qwen-plus
  api_base: https://dashscope.aliyuncs.com/compatible-mode/v1
  api_key_env: DASHSCOPE_API_KEY
```

Then:

```bash
export DASHSCOPE_API_KEY=sk-...
atlas
```

Any OpenAI-compatible `/chat/completions` endpoint works (OpenAI, DashScope compatible
mode, DeepSeek, vLLM, Ollama, ...). For DeepSeek:

```yaml
model:
  provider: openai-compatible
  name: deepseek-chat
  api_base: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
```

```bash
export DEEPSEEK_API_KEY=sk-...
atlas
```

### Terminal UI

`atlas` in a real terminal opens a full-screen Textual UI (opencode-style entry
point). Piped/non-TTY use falls back to the plain `atlas chat` loop. A visual
mockup lives at [design/atlas-ui.html](design/atlas-ui.html).

| Command | Meaning |
|---|---|
| `/help` | command help |
| `/compact` | summarize old context, keep a recent tail |
| `/new` | start a new session |
| `/sessions` | list recent sessions |
| `/know [query]` | list or search project memory |
| `/skill list` | list installed skills |
| `/skill install <path-or-url>` | install a Markdown skill |
| `/skill remove <name>` | remove a skill |
| `/doctor` | show doctor JSON |
| `/knowledge <query>` | search project knowledge |
| `/clear` | clear the screen |
| `/exit` | quit |

Type `/` to open the command menu (↑/↓ + Enter). Type `#` to open a workspace
file menu; selecting a file inserts `#path` and attaches its contents (bounded to
40k chars) to the next message. Press `Esc` once to close a menu, or `Esc Esc` to
quit.

Keys: `Ctrl+N` new session, `Ctrl+Q` quit, `F1` help, `Esc Esc` quit.

The TUI streams assistant text through the OpenAI-compatible SSE path. Tool
calls are shown as collapsed cards; click a card title to expand its result.
The transcript is a scrollable log, and long tool output scrolls inside the
expanded card. `/compact` asks the configured model to summarize older messages
and saves the reduced checkpoint. `/skill install` accepts a local `SKILL.md` or
an http(s) URL; installed skills are listed in the system prompt and loaded on
demand.

Try the bundled example:

```text
/skill install examples/skills/code-review.md
```

> The AgentScope `Agent`/`Toolkit` adapter is available but still experimental. It has
> been exercised with a fake model, a local OpenAI-compatible streaming server, and a
> real DeepSeek `deepseek-chat` run with streaming on and off. Prior user/assistant
> text turns are restored when you pass `sessions=atlas.sessions`; tool-call history
> replay and middleware are still pending.

---

## Commands

```bash
atlas init [--path .] [--name NAME]
atlas doctor
atlas demo [--keep]
atlas                    # TUI when stdin is a TTY, otherwise chat
atlas tui [--path .]     # explicit full-screen TUI
atlas chat [--session ID] [message ...]
atlas session list
atlas knowledge search "pytest"
atlas knowledge add --title "Test command" --body "python -m pytest -q" --kind command
atlas knowledge validate kn_xxx
atlas curator run [--llm]
atlas serve --port 8765
atlas eval
```

`atlas eval` runs 10 small golden tasks in isolated temp repos. The suite is
validated three ways: every task starts red (0/10 before a fix), a deterministic
reference solver turns it green (10/10), and a real DeepSeek `deepseek-chat` run
passed **10/10** with `pass_rate=1.0`. Override the model with `--model`,
`--api-base` and `--api-key-env`. Without a model, use `atlas demo` for the
offline end-to-end check.

`atlas curator run` is deterministic by default. `--llm` asks the configured model
for root-cause/prevention and falls back to the deterministic template when the
model is missing or returns unusable JSON. It has been exercised against real
DeepSeek; lessons stay `candidate` until a validation/replay step promotes them.

The daemon exposes `POST /rpc` (JSON-RPC 2.0) and `GET /health`:

```bash
curl -s http://127.0.0.1:8765/health
curl -s -X POST http://127.0.0.1:8765/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"doctor"}'
```

Set `ATLAS_TOKEN` (or `--token`) to require `Authorization: Bearer <token>`.

## VSCode extension (M0)

Minimal client lives in [`extension/`](extension/README.md):

```bash
cd extension
npm install
npm run compile
# F5 in VSCode, or:
npx @vscode/vsce package
code --install-extension atlas-vscode-0.0.1.vsix
```

Commands: `Atlas: Chat`, `Atlas: Doctor`, `Atlas: Initialize Project`,
`Atlas: Start Local Server`. It is a shell; Webview/diff/plan/task panels are next.

---

## What is implemented

| Area | Status |
|---|---|
| SQLite schema (15 tables) | ✅ |
| Session / turn / event / checkpoint | ✅ |
| Knowledge store + FTS5 search + lifecycle | ✅ |
| 12-tool surface | ✅ 10 real, 2 stubs (lsp/subagent); web search not enabled |
| Workspace path guard | ✅ |
| Dangerous-command deny list | ✅ basic |
| Test/lint/check + verification evidence | ✅ |
| OpenAI-compatible chat loop | ✅ |
| Textual TUI (opencode-style entry) | ✅ streaming, collapsible tool cards, scrollable transcript |
| Skills (local/URL `SKILL.md` install) | ✅ descriptions in prompt; loaded on demand |
| Context compaction (`/compact`) | ✅ model summary + reduced checkpoint |
| Offline end-to-end demo | ✅ |
| Curator (deterministic consolidation/report; optional LLM RCA) | 🟡 replay validation pending |
| JSON-RPC HTTP daemon (`/rpc`, `/health`) | ✅ |
| Eval harness (10 tasks; real DeepSeek 10/10, pass_rate 1.0) | ✅ |
| VSCode extension shell | 🟡 |
| AgentScope adapter | 🟡 experimental; fake/local/real DeepSeek; text-turn restore; tool-history/middleware pending |
| Multi-repo / team server | ⏳ later |

Tools currently available:

`read`, `edit`, `search`, `shell`, `git`, `check`, `knowledge`, `plan`, `ask_user`,
`web` (fetch only). `lsp` and `subagent` return a clear "not available in this milestone"
message; they are placeholders for M1/M2.

---

## Knowledge model

One `knowledge_items` table:

- `scope`: `user` / `project` / `org`
- `kind`: `preference` / `fact` / `decision` / `lesson` / `doc` / `command` / `contract` / `assumption` / `glossary`
- `status`: `candidate` / `active` / `stale` / `retired` / `quarantined`

This replaces three separate systems (`MemoryService`, `LessonStore`, `ProjectKnowledgeBase`).
The `kind` decides write validation, recall policy and TTL; the `scope` decides isolation.

---

## Optional: AgentScope adapter

The default M0 loop is OpenAI-compatible and has no AgentScope dependency. If you
want to run the same tools/knowledge through AgentScope 2.x:

```bash
pip install "agentscope>=2.0"
export DASHSCOPE_API_KEY=sk-...
python examples/agentscope_smoke.py
```

See `src/atlas/agentscope_adapter.py`. It is experimental: it wraps the same 12
tools as `ToolBase` instances and runs one `Agent.reply` turn. In async code, call
`build_agent` and `await agent.reply(...)` directly; `run_once` is only a sync
wrapper. Tests cover tool roundtrips, the OpenAI-compatible streaming path, and a
real DeepSeek `deepseek-chat` run. Pass `sessions=atlas.sessions` to restore prior
user/assistant text turns and record new ones; tool-call history and middleware are
still on the M0 task board.

For DeepSeek, use AgentScope's OpenAI-compatible model:

```python
import os

from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel

from atlas.agentscope_adapter import run_once

model = OpenAIChatModel(
    credential=OpenAICredential(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
    ),
    model="deepseek-chat",
)
print(run_once(model, tools, atlas.config, session["id"], "read AGENTS.md"))
```

## Development

```bash
pytest -q
```

Install the package first (`pip install -e ".[dev]"`) so `atlas` is importable.

---

## Layout

```text
src/atlas/
  cli.py            # atlas init/doctor/demo/chat/knowledge/session
  orchestrator.py   # Atlas facade
  agent.py          # AgentRuntime + tool loop
  llm.py            # OpenAI-compatible client
  tools.py          # 12-tool registry
  workspace.py      # safe read/edit/search/shell/git/check
  knowledge.py      # unified knowledge service
  sessions.py       # sessions/turns/events/checkpoints
  db.py             # SQLite schema
  config.py         # atlas.yaml
  demo.py           # offline end-to-end demo
tests/
```

---

## Known limits (by design)

- Single repository, single user, local only.
- No multi-tenant server, SSO, RBAC, audit export.
- No self-evolution (L3–L6).
- No multi-repo/service graph.
- `shell` deny list is a guard, not a security boundary; use Docker for untrusted repos.
- `web` fetch is basic HTML-to-text; no search provider.
- The TUI has basic collapsible tool cards; diff/plan panels and full interrupt handling are pending.
- The AgentScope adapter is experimental; text-turn restoration works, but tool-call history and middleware are pending.

The point of this milestone is to make the **durable core** runnable and testable before
adding more nouns. See `docs/atlas-maturity-roadmap.md` for the path from MVP to production.