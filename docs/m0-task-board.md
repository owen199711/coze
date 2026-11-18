# M0 Task Board

> Scope: Lean Atlas v1, Python M0.
> Status: **pytest 140 passed (2026-11-18); max_turns semantics aligned with ADR-0002; memory recall/capture/replay hardened (ADR-0004); security/recovery hardening applied; 10-task eval repeatable and red-baseline checked; historical DeepSeek 10/10 is not reproducible offline; curator LLM RCA opt-in with deterministic fallback; AgentScope text-turn restoration; wheel + VSIX built.** VSIX installation, curator idle scheduler, and AgentScope tool-history/middleware are not verified yet.
> Rule: one task = one verifiable change; no new top-level service/table/tool without an ADR.

## Completion summary (2026-11-17)

- ✅ verified by execution: 15 tasks (M0-01..12, 12a, 15, 16)
- 🟦 code written / partial verification: 0 tasks
- 🟡 partial or needs IDE/scale validation: 2 tasks (M0-13, 14)
- ⏳ not started: 0
- **M0 core is verified**: `pytest -q` → 140 passed; recovery window/concurrent turns/knowledge validation/daemon auth/TUI tests added; 10-task fixtures validated red→green; curator LLM RCA fake-model/fallback validated. Historical DeepSeek results are kept as session notes, not a reproducible claim.
- **2026-11-18 memory hardening (ADR-0004)**: query-aware `recall()` with citations/per-kind budgets, conservative candidate capture, candidate-first agent writes, quarantine revival gate, replay/test-only agent promotion, transactional writes, escaped LIKE fallback, failure-signature dedupe, `curator run --replay` promotion, source-hash refresh, TTL for fact/command/contract, Curator single-flight/per-run LLM budget, secret scan, untrusted recall filtering, and hybrid FTS5+chunk retrieval.
- **2026-11-18 hardening**: latest-window `turns/events`; `UNIQUE(session_id, seq)` + `BEGIN IMMEDIATE` + daemon per-session lock; sensitive-path guard and read-before-edit; git main/secret guards; shell env secret scrubbing; SSRF rejection; knowledge promotion requires validation and dedupe merges state; `check` records verifications; cost/price fields, `max_cost_usd` enforcement, checkpoint retention; `MaxTurnsExceeded` (ADR-0002); permission approval gate (ADR-0003); TUI sensitive-file and busy-session fixes; extension SecretStorage/timeout/stale-session refresh; CI workflow.
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
| M0-10 | Run tests & fix failures | ✅ | `tests/` | 140 passed |
| M0-11 | Smoke `init/doctor/demo` on a clean temp dir | ✅ | — | demo `ok: true`; doctor base_tables = 15 |
| M0-12 | AgentScope `Agent` adapter (experimental, one turn) | ✅ | `src/atlas/agentscope_adapter.py` | fake model + local SSE + real DeepSeek `run_once`; text turns restored/recorded; tool-history/middleware pending |
| M0-12a | JSON-RPC HTTP daemon (`/rpc`, `/health`) | ✅ | `src/atlas/daemon.py` | doctor/session/knowledge dispatch |
| M0-13 | VSCode extension shell (Chat/Doctor/Init/Server) | 🟡 | `extension/` | compiled + VSIX built; not installed in IDE |
| M0-14 | CuratorJob (deterministic consolidation + optional LLM RCA + replay) | 🟡 | `src/atlas/curator.py` | deterministic + fake-model + LLM RCA + replay promotion; single-flight implemented; idle scheduler next |
| M0-15 | Packaging (wheel + VSIX + install doc) | ✅ | `dist/`, `extension/*.vsix` | clean venv install + demo passed |
| M0-16 | 10 golden tasks + baseline metrics | ✅ | `src/atlas/eval.py` | 10 tasks; buggy 0/10; `calc_add` reference solver; `--repeat` attempts; historical DeepSeek 10/10 not reproducible offline |

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

### 2. Curator idle scheduling / budget

`atlas curator run --llm --replay` re-runs the recorded failed command and promotes
a candidate only when the replay passes; failure candidates are deduped by
signature, and `jobs` provides single-flight. Next:
- add idle/nightly scheduling and per-night budget;
- attach replay metrics to the report;
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
pytest -q                        140 passed
atlas init --path <tmp>          ok
atlas doctor --path <tmp>        base_tables=15, fts_tables=1, fts5=true
atlas demo                       red_exit=1, green_exit=0, ok=true
atlas (no args)                  TTY -> Textual TUI; pipe -> chat
atlas tui                        TUI mount; / palette; # file; Esc Esc; fake model
TUI streaming                    chat_stream + on_text/on_event
TUI tool cards                   collapsed by default; spinner while running; expandable
/session                        ↑/↓ session picker; Enter switches session
/compact                        model summary + reduced checkpoint
/skill                          install/list/remove + prompt injection
/know                           list/search active memory
read >4k                         bounded content preview; no blind retry
tool loop guard                  duplicate suppression + max_turns exception
atlas curator run                report written; learning.enabled honored
atlas curator run --llm          no model -> new_candidates=1, fallback=1, llm_error set
atlas curator run --replay       passing replay -> candidate promoted; failure stays candidate
curator failure signature        two identical failed verifications -> one candidate
source hash refresh              changed file-backed doc -> stale
TTL                              old unused fact/command/contract -> stale
read result budget               12k chars/400 lines; next_start; 16k model-facing
tool approval                    default mode allows read-only actions; writes prompt
web redirect                     private redirect target rejected
git secret scan                  staged ghp_/AKIA/private-key patterns rejected
search guard                     long/nested-quantifier regex rejected; time-bounded
shell timeout                    process group terminated on timeout
secret/trust                     secret content rejected; untrusted not recalled by default
curator budget                   exhausted -> deterministic RCA fallback
hybrid chunk retrieval           FTS5 + knowledge_chunks -> origin=hybrid/chunk
curator LLM RCA (fake model)     root cause/prevention candidate; validation=unvalidated
curator LLM RCA (HTTP mock)      real OpenAI client parsed RCA; unvalidated candidate
curator LLM RCA + DeepSeek       historical session note; unvalidated
atlas serve + /health + /rpc     health=true, doctor=15, auth/lock tests ok
npm run compile                  ok
vsce package                     historical VSIX; rebuild needed after TS changes
pip wheel .                      atlas_agent-0.1.0-py3-none-any.whl
clean venv install + demo        ok
AgentScope fake-model roundtrip  read tool result + knowledge search ok
AgentScope text-turn restore     prior context restored; new turns recorded
AgentScope local OpenAI SSE      stream=true, tool call + final text ok
atlas chat + DeepSeek            historical session note; checkpoint; 2 turns
DeepSeek stream                  historical session note; chat_stream + on_text
AgentScope + DeepSeek run_once   historical session note; stream=False/True
AgentScope + DeepSeek tool call  historical session note; read pyproject.toml ok
eval fixtures (10 tasks)         buggy 0/10; calc_add reference solver passes
atlas eval (no model)            repeatable attempts; 0 passed, exit=1
eval harness + DeepSeek          historical 10/10 (session note; not reproducible offline)
atlas eval CLI + DeepSeek        historical 10/10; exit=0 (session note)
```

## Known blockers / unverified

- VSCode install/F5: no `code` CLI found on PATH or in the usual install locations; VSIX built but not installed.
- Curator replay promotion: `atlas curator run --replay` works; single-flight is
  implemented via `jobs`; idle scheduling and per-night budget are still pending.
- AgentScope adapter: one user turn per `run_once`; prior text turns restore, but tool-call history and middleware are pending.
- S2/S3/S4 (team/production/maturity) are not started.

