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


def test_knowledge_list_surfaces_candidates(tmp_path, capsys) -> None:
    """Candidates must be listable, otherwise `knowledge validate` has no id to take.

    `search()` hardcodes `status = 'active'` and `list()` defaults to
    `status="active"`, so curator-mined lessons were unreachable from the CLI.
    """
    from atlas.knowledge import KnowledgeItem
    from atlas.orchestrator import Atlas

    atlas = Atlas.init(tmp_path, name="list-candidates")
    try:
        item_id = atlas.knowledge.write(
            KnowledgeItem(
                title="Retry flaky test",
                body="Re-run the suite once before blaming the change.",
                kind="lesson",
                scope="project",
                status="candidate",
                project_id=atlas.project_id,
            )
        )
    finally:
        atlas.close()

    assert main(["knowledge", "list", "--path", str(tmp_path), "--status", "candidate"]) == 0
    out = capsys.readouterr().out
    assert item_id in out
    assert "Retry flaky test" in out

    # Default status stays "active", so candidates do not leak into normal recall.
    assert main(["knowledge", "list", "--path", str(tmp_path)]) == 0
    assert item_id not in capsys.readouterr().out
