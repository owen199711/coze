from __future__ import annotations

import threading
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


def test_turns_and_events_return_the_latest_window(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="test-window")
    session_id = atlas.create_session("window")["id"]
    for index in range(120):
        atlas.sessions.append_turn(session_id, "user", f"u{index}")
        atlas.sessions.append_turn(session_id, "assistant", f"a{index}")

    turns = atlas.sessions.turns(session_id)
    assert len(turns) == 100
    assert turns[0]["content"] == "u70"
    assert turns[-1]["content"] == "a119"

    state = atlas.sessions.load_state(session_id)
    assert state is not None
    assert state["messages"][-1]["content"] == "a119"

    for index in range(150):
        atlas.sessions.event(session_id, None, "probe.event", {"i": index})
    events = atlas.sessions.events(session_id)
    assert len(events) == 100
    assert events[0]["payload"]["i"] == 50
    assert events[-1]["payload"]["i"] == 149
    atlas.close()


def test_concurrent_append_turn_keeps_seq_unique(tmp_path: Path) -> None:
    first = Atlas.init(tmp_path, name="concurrent")
    second = Atlas(tmp_path)
    try:
        session_id = first.create_session("concurrent")["id"]

        def worker(atlas: Atlas, prefix: str) -> None:
            for index in range(10):
                atlas.sessions.append_turn(session_id, "user", f"{prefix}{index}")

        threads = [
            threading.Thread(target=worker, args=(first, "a")),
            threading.Thread(target=worker, args=(second, "b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        turns = first.sessions.turns(session_id, limit=1000)
        seqs = [turn["seq"] for turn in turns]
        assert len(turns) == 20
        assert len(set(seqs)) == 20
        assert seqs == list(range(1, 21))
    finally:
        first.close()
        second.close()


def test_checkpoints_are_retained_in_a_bounded_window(tmp_path: Path) -> None:
    atlas = Atlas.init(tmp_path, name="checkpoint-window")
    try:
        session_id = atlas.create_session("checkpoints")["id"]
        for index in range(60):
            atlas.sessions.save_checkpoint(
                session_id,
                None,
                {"messages": [{"role": "user", "content": str(index)}]},
            )
        rows = atlas.db.fetchall(
            "SELECT COUNT(*) AS n FROM checkpoints WHERE session_id = ?",
            (session_id,),
        )
        assert rows[0]["n"] == atlas.sessions.CHECKPOINT_KEEP
        latest = atlas.sessions.latest_checkpoint(session_id)
        assert latest is not None
        assert latest["state"]["messages"][0]["content"] == "59"
    finally:
        atlas.close()


def test_demo_end_to_end() -> None:
    from atlas.demo import run_demo

    summary = run_demo()
    assert summary["red_exit"] not in (None, 0)
    assert summary["green_exit"] == 0
    assert summary["knowledge"]
    assert summary["knowledge_hits"] >= 1
    assert summary["ok"] is True
