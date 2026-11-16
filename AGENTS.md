# AGENTS.md

## Project
Lean Atlas: a Python MVP of a local-first coding agent.
Read `docs/lean-atlas-v1.md` before changing architecture.
Keep the durable core small: one knowledge store, one session journal, one execution service.

## Commands
- Install: `pip install -e ".[dev]"`
- Test: `pytest -q`
- Demo: `atlas demo`
- Lint: `ruff check src tests` (if installed)
- Format: `ruff format src tests` (if installed)

## Rules
- Do not add a new top-level service, table, middleware, tool, or UI panel without an ADR.
- Prefer merging into `KnowledgeService` over adding `MemoryService`/`LessonStore`/`PKB`.
- Read a file before editing it.
- Write a failing test before implementing a behaviour change.
- Do not commit secrets, `.env`, `.atlas/`, or generated artifacts.
- Do not force-push or edit `main` directly.

## Definition of Done
- `pytest -q` passes.
- The change is described with evidence: commands, outputs, files.
- If a new capability is added, one old thing is removed or explicitly deferred.
