from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .curator import CuratorJob
from .daemon import serve
from .demo import run_demo
from .eval import run_suite
from .knowledge import VALID_KINDS, VALID_SCOPES
from .llm import ModelError
from .orchestrator import Atlas


def _print(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _ask_user(question: str, options: list[str], recommended: str | None) -> str:
    print(f"\n{question}")
    for idx, option in enumerate(options, 1):
        mark = " (recommended)" if recommended and option == recommended else ""
        print(f"  {idx}. {option}{mark}")
    print("  0. not sure / let Atlas choose the recommended default")
    raw = input("> ").strip()
    if raw == "0" or not raw:
        return recommended or (options[0] if options else "")
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    return raw


def cmd_init(args: argparse.Namespace) -> int:
    atlas = Atlas.init(args.path, name=args.name)
    _print(
        {
            "ok": True,
            "root": str(atlas.config.root),
            "config": str(atlas.config.root / "atlas.yaml"),
            "db": str(atlas.config.db_path),
            "next": "atlas doctor && atlas demo",
        }
    )
    atlas.close()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    atlas = Atlas(args.path)
    _print(atlas.doctor())
    atlas.close()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    summary = run_demo(base=args.path, keep=args.keep)
    _print(summary)
    return 0 if summary.get("ok") else 1


def cmd_chat(args: argparse.Namespace) -> int:
    atlas = Atlas(args.path)
    session_id = args.session
    if not session_id:
        session = atlas.create_session("CLI session")
        session_id = session["id"]
    print(f"session: {session_id} (type /exit to quit)")
    try:
        if args.message:
            print(atlas.chat(session_id, " ".join(args.message), ask_user=_ask_user))
            return 0
        while True:
            try:
                message = input("you> ").strip()
            except EOFError:
                break
            if not message:
                continue
            if message in {"/exit", "/quit"}:
                break
            try:
                print(atlas.chat(session_id, message, ask_user=_ask_user))
            except (ConfigError, ModelError) as exc:
                print(f"error: {exc}")
                print("hint: configure model.name/api_key_env in atlas.yaml, or run `atlas demo`.")
                return 2
    finally:
        atlas.close()
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    atlas = Atlas(args.path)
    _print(atlas.list_sessions(limit=args.limit))
    atlas.close()
    return 0


def cmd_knowledge_search(args: argparse.Namespace) -> int:
    atlas = Atlas(args.path)
    hits = atlas.knowledge_search(args.query, limit=args.limit)
    _print(
        [
            {
                "id": hit.item.id,
                "kind": hit.item.kind,
                "scope": hit.item.scope,
                "title": hit.item.title,
                "snippet": hit.snippet,
                "confidence": hit.item.confidence,
            }
            for hit in hits
        ]
    )
    atlas.close()
    return 0


def cmd_knowledge_add(args: argparse.Namespace) -> int:
    atlas = Atlas(args.path)
    item_id = atlas.remember(
        title=args.title,
        body=args.body,
        kind=args.kind,
        scope=args.scope,
        source="cli",
    )
    _print({"ok": True, "id": item_id})
    atlas.close()
    return 0


def cmd_knowledge_validate(args: argparse.Namespace) -> int:
    atlas = Atlas(args.path)
    ok = atlas.knowledge.promote(args.id)
    _print({"ok": ok, "id": args.id})
    atlas.close()
    return 0 if ok else 1


def cmd_curator(args: argparse.Namespace) -> int:
    atlas = Atlas(args.path)
    result = CuratorJob(atlas).run_once(use_llm=getattr(args, "llm", False))
    _print(result)
    atlas.close()
    return 0 if result.get("ok") else 1


def cmd_serve(args: argparse.Namespace) -> int:
    serve(args.path, host=args.host, port=args.port, token=args.token)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    config = load_config(args.path)
    model_name = args.model or config.model_name or os.environ.get("ATLAS_MODEL", "")
    overrides = None
    if model_name:
        overrides = {
            "model_name": model_name,
            "api_base": args.api_base or config.api_base,
            "api_key_env": args.api_key_env or config.api_key_env,
            "max_turns": config.max_turns,
        }
    result = run_suite(keep=args.keep, config_overrides=overrides)
    _print(result)
    return 0 if result.get("failed", 1) == 0 else 1


def tui_available() -> bool:
    try:
        from .tui import is_available

        return is_available()
    except Exception:
        return False


def cmd_tui(args: argparse.Namespace) -> int:
    try:
        from .tui import run_tui
    except ImportError:
        print(
            "Atlas TUI requires textual. Install it with: pip install textual",
            file=sys.stderr,
        )
        return 2
    return run_tui(args.path, args.session)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas", description="Lean Atlas coding agent")
    parser.add_argument("--version", action="version", version=f"atlas {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create atlas.yaml, AGENTS.md, and the local database")
    init.add_argument("--path", default=".")
    init.add_argument("--name")
    init.set_defaults(func=cmd_init)

    doctor = sub.add_parser("doctor", help="check config, database, git, FTS5 and model setup")
    doctor.add_argument("--path", default=".")
    doctor.set_defaults(func=cmd_doctor)

    demo = sub.add_parser("demo", help="run the offline end-to-end demo (no LLM required)")
    demo.add_argument("--path", default=None)
    demo.add_argument("--keep", action="store_true", help="keep the temporary demo repository")
    demo.set_defaults(func=cmd_demo)

    chat = sub.add_parser("chat", help="chat with the configured model")
    chat.add_argument("--path", default=".")
    chat.add_argument("--session")
    chat.add_argument("message", nargs="*")
    chat.set_defaults(func=cmd_chat)

    sessions = sub.add_parser("session", help="list sessions")
    sessions_sub = sessions.add_subparsers(dest="session_command", required=True)
    sessions_list = sessions_sub.add_parser("list")
    sessions_list.add_argument("--path", default=".")
    sessions_list.add_argument("--limit", type=int, default=20)
    sessions_list.set_defaults(func=cmd_sessions)

    knowledge = sub.add_parser("knowledge", help="search or add project knowledge")
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_command", required=True)
    knowledge_search = knowledge_sub.add_parser("search")
    knowledge_search.add_argument("query")
    knowledge_search.add_argument("--path", default=".")
    knowledge_search.add_argument("--limit", type=int, default=8)
    knowledge_search.set_defaults(func=cmd_knowledge_search)

    knowledge_add = knowledge_sub.add_parser("add")
    knowledge_add.add_argument("--title", required=True)
    knowledge_add.add_argument("--body", required=True)
    knowledge_add.add_argument("--kind", default="fact", choices=sorted(VALID_KINDS))
    knowledge_add.add_argument("--scope", default="project", choices=sorted(VALID_SCOPES))
    knowledge_add.add_argument("--path", default=".")
    knowledge_add.set_defaults(func=cmd_knowledge_add)

    knowledge_validate = knowledge_sub.add_parser("validate")
    knowledge_validate.add_argument("id")
    knowledge_validate.add_argument("--path", default=".")
    knowledge_validate.set_defaults(func=cmd_knowledge_validate)

    curator = sub.add_parser("curator", help="run the knowledge curator (deterministic by default)")
    curator_sub = curator.add_subparsers(dest="curator_command", required=True)
    curator_run = curator_sub.add_parser("run")
    curator_run.add_argument("--path", default=".")
    curator_run.add_argument(
        "--llm",
        action="store_true",
        help="ask the configured model for root-cause analysis (falls back to deterministic)",
    )
    curator_run.set_defaults(func=cmd_curator)

    serve = sub.add_parser("serve", help="run the JSON-RPC HTTP daemon")
    serve.add_argument("--path", default=".")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--token", default=None)
    serve.set_defaults(func=cmd_serve)

    tui = sub.add_parser("tui", help="open the full-screen terminal UI")
    tui.add_argument("--path", default=".")
    tui.add_argument("--session")
    tui.set_defaults(func=cmd_tui)

    eval_parser = sub.add_parser("eval", help="run the M0 golden task suite")
    eval_parser.add_argument("--path", default=".", help="project config to read the model from")
    eval_parser.add_argument("--model", default=None, help="model name override")
    eval_parser.add_argument("--api-base", default=None, help="OpenAI-compatible API base override")
    eval_parser.add_argument("--api-key-env", default=None, help="environment variable holding the API key")
    eval_parser.add_argument("--keep", action="store_true", help="keep temp repos for inspection")
    eval_parser.set_defaults(func=cmd_eval)

    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    args_list = list(sys.argv[1:] if argv is None else argv)
    # `atlas` with no subcommand: a real TTY gets the opencode-style TUI;
    # pipes/scripts get the plain chat loop.
    if not args_list:
        interactive = False
        try:
            interactive = sys.stdin.isatty() and sys.stdout.isatty()
        except Exception:
            pass
        args_list = ["tui"] if interactive and tui_available() else ["chat"]
    parser = build_parser()
    args = parser.parse_args(args_list)
    try:
        return int(args.func(args))
    except (ConfigError, ModelError, ValueError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
