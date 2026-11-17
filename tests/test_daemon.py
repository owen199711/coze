from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from atlas.config import ConfigError
from atlas.daemon import AtlasRPC, _Handler, serve
from atlas.orchestrator import Atlas


def test_rpc_dispatch(tmp_path: Path) -> None:
    init_atlas = Atlas.init(tmp_path, name="rpc-test")
    init_atlas.close()
    rpc = AtlasRPC(str(tmp_path))

    doctor = rpc.dispatch("doctor")
    assert doctor["base_tables"] >= 15

    session = rpc.dispatch("session.create", {"title": "rpc"})
    assert session["id"]
    assert rpc.dispatch("session.get", {"session_id": session["id"]})["id"] == session["id"]

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


def test_serve_refuses_public_bind_without_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ATLAS_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="non-loopback"):
        serve(str(tmp_path), host="0.0.0.0", port=0)


def test_rpc_requires_bearer_token(tmp_path: Path) -> None:
    init_atlas = Atlas.init(tmp_path, name="rpc-auth")
    init_atlas.close()
    rpc = AtlasRPC(str(tmp_path))
    handler = type("AuthHandler", (_Handler,), {"rpc": rpc, "token": "secret"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/rpc"
    body = {"jsonrpc": "2.0", "id": 1, "method": "doctor", "params": {}}
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request, timeout=5)
        assert exc.value.code == 401

        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["result"]["base_tables"] >= 15
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
