from __future__ import annotations

from pathlib import Path

from atlas.orchestrator import Atlas


def test_session_checkpoint_and_events(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="test")
    session = atlas.create_session("test session")
    session_id = session["id"]

    turn_id = atlas.sessions.append_turn(session_id, "user", "hello")
    atlas.sessions.event(session_id, turn_id, "test.event", {"ok": True})
    atlas.sessions.save_checkpoint(
        session_id, turn_id, {"messages": [{"role": "user", "content": "hello"}]}
    )

    state = atlas.sessions.load_state(session_id)
    assert state is not None
    assert state["messages"][0]["content"] == "hello"
    assert atlas.sessions.events(session_id)[0]["type"] == "test.event"
    atlas.close()


def test_demo_end_to_end() -> None:
    from atlas.demo import run_demo

    summary = run_demo()
    assert summary["red_exit"] not in (None, 0)
    assert summary["green_exit"] == 0
    assert summary["knowledge"]
    assert summary["knowledge_hits"] >= 1
    assert summary["ok"] is True
