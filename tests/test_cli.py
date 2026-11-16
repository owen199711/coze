from __future__ import annotations

from atlas.cli import main


def test_main_without_subcommand_defaults_to_chat(monkeypatch) -> None:
    seen = {}

    def fake_chat(args):
        seen["session"] = args.session
        seen["message"] = args.message
        return 0

    monkeypatch.setattr("atlas.cli.cmd_chat", fake_chat)
    assert main([]) == 0
    assert seen == {"session": None, "message": []}


def test_main_without_subcommand_uses_tui_for_a_tty(monkeypatch) -> None:
    class Tty:
        def isatty(self) -> bool:
            return True

    seen = {}

    def fake_tui(args):
        seen["path"] = args.path
        seen["session"] = args.session
        return 0

    monkeypatch.setattr("atlas.cli.sys.stdin", Tty())
    monkeypatch.setattr("atlas.cli.sys.stdout", Tty())
    monkeypatch.setattr("atlas.cli.tui_available", lambda: True)
    monkeypatch.setattr("atlas.cli.cmd_tui", fake_tui)
    assert main([]) == 0
    assert seen == {"path": ".", "session": None}
