# M0 Task Board

> Scope: Lean Atlas v1, Python M0.
> Status: **pytest 63 passed; 10-task eval real DeepSeek 10/10 (pass_rate=1.0); curator LLM RCA opt-in with deterministic fallback and real DeepSeek validation; AgentScope text-turn restoration; wheel + VSIX built.** VSIX installation, curator replay validation, and AgentScope tool-history/middleware are not verified yet.
> Rule: one task = one verifiable change; no new top-level service/table/tool without an ADR.

## Completion summary (2026-11-17)

- ✅ verified by execution: 15 tasks (M0-01..12, 12a, 15, 16)
- 🟦 code written / partial verification: 0 tasks
- 🟡 partial or needs IDE/scale validation: 2 tasks (M0-13, 14)
- ⏳ not started: 0
- **M0 core is verified**: `pytest -q` → 63 passed; real DeepSeek chat + AgentScope `run_once` + 10/10 eval; 10-task fixtures validated red→green; curator LLM RCA fake-model/fallback/real-DeepSeek validated.
- **Not yet verified**: VSIX installation inside VSCode, curator replay promotion, AgentScope tool-history/middleware.

## Status legend

- ✅ verified by execution
- 🟦 code written; static review only, not executed
- 🟡 partial / needs work
- ⏳ not started
- ⛔ deferred (not M0)

## Tasks

| ID | Task | Status | Files | Acceptance |
|---|---|---|---|---|
| M0-01 | Project scaffold (`pyproject`, src layout, script entry) | ✅ | `pyproject.toml` | `pip install -e ".[dev]"` succeeds |
| M0-02 | SQLite schema (15 tables) | ✅ | `src/atlas/db.py` | `atlas init` creates DB; 15 base tables |
| M0-03 | Session / turn / event / checkpoint | ✅ | `src/atlas/sessions.py` | create → append → checkpoint → resume |
| M0-04 | Unified knowledge store (search/get/write/feedback/promote/forget) | ✅ | `src/atlas/knowledge.py` | project isolation; lesson candidate not recalled |
| M0-05 | Workspace guard + 10 real tools | ✅ | `src/atlas/workspace.py`, `tools.py` | unit tests + demo + real read/edit/test loop |
| M0-06 | Agent loop (OpenAI-compatible) | ✅ | `src/atlas/agent.py`, `llm.py` | real DeepSeek tool call → result → final; checkpoint saved |
| M0-07 | CLI (`init/doctor/demo/chat/tui/session/knowledge`) | ✅ | `src/atlas/cli.py` | real `atlas chat` + Textual TUI + demo; errors return 2 |
| M0-08 | Offline end-to-end demo | ✅ | `src/atlas/demo.py` | red exit ≠ 0, green exit = 0, knowledge hit |
| M0-09 | Tests written | ✅ | `tests/` | `pytest -q` passes |
| M0-10 | Run tests & fix failures | ✅ | `tests/` | 63 passed |
| M0-11 | Smoke `init/doctor/demo` on a clean temp dir | ✅ | — | demo `ok: true`; doctor base_tables = 15 |
| M0-12 | AgentScope `Agent` adapter (experimental, one turn) | ✅ | `src/atlas/agentscope_adapter.py` | fake model + local SSE + real DeepSeek `run_once`; text turns restored/recorded; tool-history/middleware pending |
| M0-12a | JSON-RPC HTTP daemon (`/rpc`, `/health`) | ✅ | `src/atlas/daemon.py` | doctor/session/knowledge dispatch |
| M0-13 | VSCode extension shell (Chat/Doctor/Init/Server) | 🟡 | `extension/` | compiled + VSIX built; not installed in IDE |
| M0-14 | CuratorJob (deterministic consolidation + optional LLM RCA) | 🟡 | `src/atlas/curator.py` | deterministic + fake-model + real DeepSeek LLM RCA + fallback; replay promotion next |
| M0-15 | Packaging (wheel + VSIX + install doc) | ✅ | `dist/`, `extension/*.vsix` | clean venv install + demo passed |
| M0-16 | 10 golden tasks + baseline metrics | ✅ | `src/atlas/eval.py` | 10 tasks; buggy 0/10, reference solver 10/10; real DeepSeek 10/10, pass_rate=1.0 |

## Next small tasks (in order)

### 1. VSCode extension install

```bash
cd extension
npm install
npm run compile
code --install-extension atlas-vscode-0.0.1.vsix
```

Acceptance: `Atlas: Start Local Server`, then `Atlas: Chat` returns an answer
inside VSCode. (This machine has no `code` CLI on PATH, so this is unverified.)

### 2. Curator replay validation

LLM RCA is implemented as `atlas curator run --llm` (deterministic fallback when
no model is configured or the model returns unusable JSON) and has been exercised
against real DeepSeek. Next:
- replay the original failed verification, or a targeted regression test;
- attach the replay result to `KnowledgeItem.validation`;
- keep candidates out of recall until validation passes.
Acceptance: a candidate cannot be promoted without a recorded replay result.

### 3. TUI polish and tool cards

Streaming replies, `/compact`, `/know`, `/skill`, `/` command palette, `#` file
picker, double-Esc, and collapsible scrollable tool cards are implemented. Next:
- add diff/plan panels, model/session picker, and interrupt keybinding;
- persist TUI view state and add a theme.
Acceptance: a long answer streams before completion and tool activity is visible
without opening the session journal.

## Verification checklist for every task

- [ ] One user-visible change.
- [ ] `pytest -q` passes (or the task explicitly has no tests and explains why).
- [ ] `atlas doctor` still works.
- [ ] No new top-level service/table/tool.
- [ ] If a new noun was added, one old noun was removed or deferred.
- [ ] The final answer includes the command + output evidence.

## Verified in this session

```text
pytest -q                        63 passed
atlas init --path <tmp>          ok
atlas doctor --path <tmp>        base_tables=15, fts5=true
atlas demo                       red_exit=1, green_exit=0, ok=true
atlas (no args)                  TTY -> Textual TUI; pipe -> chat
atlas tui                        TUI mount; / palette; # file; Esc Esc; fake model
TUI streaming                    chat_stream + on_text/on_event
TUI tool cards                   collapsed by default; expandable; scrollable body
/compact                        model summary + reduced checkpoint
/skill                          install/list/remove + prompt injection
/know                           list/search active memory
read >4k                         bounded content preview; no blind retry
tool loop guard                  duplicate suppression + final no-tools synthesis
atlas curator run                report written
atlas curator run --llm          no model -> new_candidates=1, fallback=1, llm_error set
curator LLM RCA (fake model)     root cause/prevention candidate; validation=unvalidated
curator LLM RCA (HTTP mock)      real OpenAI client parsed RCA; unvalidated candidate
curator LLM RCA + DeepSeek       llm_analyzed=1; root cause/prevention; unvalidated
atlas serve + /health + /rpc     health=true, doctor=15, knowledge add/search ok
npm run compile                  ok
vsce package                     atlas-vscode-0.0.1.vsix
pip wheel .                      atlas_agent-0.1.0-py3-none-any.whl
clean venv install + demo        ok
AgentScope fake-model roundtrip  read tool result + knowledge search ok
AgentScope text-turn restore     prior context restored; new turns recorded
AgentScope local OpenAI SSE      stream=true, tool call + final text ok
atlas chat + DeepSeek            read tool -> "# AGENTS.md"; checkpoint; 2 turns
DeepSeek stream                  chat_stream + on_text -> "OK"
AgentScope + DeepSeek run_once   stream=False/True -> "deepseek-smoke"
AgentScope + DeepSeek tool call  read pyproject.toml status=ok
eval fixtures (10 tasks)         buggy 0/10; reference solver 10/10
atlas eval (no model)            10 tasks enumerated, 0 passed, exit=1
wheel in clean venv              eval_tasks=10; demo ok=true
eval harness + DeepSeek          10/10 passed, pass_rate=1.0
atlas eval CLI + DeepSeek        10/10; exit=0; 10 isolated temp repos
```

## Known blockers / unverified

- VSCode install/F5: no `code` CLI found on PATH or in the usual install locations; VSIX built but not installed.
- Curator replay promotion: real DeepSeek RCA works; replay result + promotion gate are pending.
- AgentScope adapter: one user turn per `run_once`; prior text turns restore, but tool-call history and middleware are pending.
- S2/S3/S4 (team/production/maturity) are not started.

