from __future__ import annotations

from pathlib import Path

from atlas.daemon import AtlasRPC
from atlas.orchestrator import Atlas


def test_rpc_dispatch(tmp_path: Path) -> None:
    init_atlas = Atlas.init(tmp_path, name="rpc-test")
    init_atlas.close()
    rpc = AtlasRPC(str(tmp_path))

    doctor = rpc.dispatch("doctor")
    assert doctor["base_tables"] >= 15

    session = rpc.dispatch("session.create", {"title": "rpc"})
    assert session["id"]

    added = rpc.dispatch(
        "knowledge.add",
        {
            "title": "RPC fact",
            "body": "The daemon can add knowledge.",
            "kind": "fact",
            "scope": "project",
        },
    )
    assert added["id"]

    hits = rpc.dispatch("knowledge.search", {"query": "daemon"})
    assert hits


def test_rpc_curator_run_without_model_falls_back(tmp_path: Path) -> None:
    init_atlas = Atlas.init(tmp_path, name="rpc-curator")
    init_atlas.close()
    rpc = AtlasRPC(str(tmp_path))

    result = rpc.dispatch("curator.run", {"llm": True})

    assert result["ok"] is True
    assert result["new_candidates"] == 0
    assert result["llm_analyzed"] == 0
    assert "no model configured" in (result["llm_error"] or "")
