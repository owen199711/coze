from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from atlas.orchestrator import Atlas
from atlas.tools import AtlasTools


def _chunk(delta: dict, finish: str | None = None, usage: dict | None = None) -> dict:
    payload = {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "fake",
        "choices": (
            []
            if usage
            else [{"index": 0, "delta": delta, "finish_reason": finish}]
        ),
    }
    if usage:
        payload["usage"] = usage
    return payload


def test_agentscope_adapter_with_openai_compatible_stream(
    tmp_path: Path, monkeypatch
) -> None:
    # A machine-wide HTTP proxy can otherwise hijack 127.0.0.1 requests.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    pytest.importorskip("agentscope")
    pytest.importorskip("openai")
    from agentscope.credential import OpenAICredential
    from agentscope.model import OpenAIChatModel

    from atlas.agentscope_adapter import run_once

    class Handler(BaseHTTPRequestHandler):
        requests: list[dict] = []

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            type(self).requests.append(body)
            has_tool_result = any(
                message.get("role") == "tool" for message in body.get("messages", [])
            )
            if not body.get("stream"):
                self.send_error(400, "expected a streaming request")
                return

            if has_tool_result:
                chunks = [
                    _chunk({"role": "assistant", "content": "read "}),
                    _chunk({"content": "ok"}),
                    _chunk({}, finish="stop"),
                    _chunk(
                        {},
                        usage={
                            "prompt_tokens": 1,
                            "completion_tokens": 2,
                            "total_tokens": 3,
                        },
                    ),
                ]
            else:
                chunks = [
                    _chunk(
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read",
                                        "arguments": json.dumps(
                                            {"path": "AGENTS.md"}
                                        ),
                                    },
                                }
                            ],
                        }
                    ),
                    _chunk({}, finish="tool_calls"),
                    _chunk(
                        {},
                        usage={
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    ),
                ]

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for item in chunks:
                self.wfile.write(f"data: {json.dumps(item)}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    atlas = None
    try:
        (tmp_path / "AGENTS.md").write_text(
            "# Probe rules\nBe careful.\n", encoding="utf-8"
        )
        atlas = Atlas.init(tmp_path, name="agentscope-openai-test")
        session = atlas.create_session("agentscope")
        tools = AtlasTools(
            atlas.workspace, atlas.knowledge, atlas.config, session["id"]
        )
        model = OpenAIChatModel(
            credential=OpenAICredential(
                api_key="sk-test",
                base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
            ),
            model="fake",
            stream=True,
        )

        result = run_once(
            model, tools, atlas.config, session["id"], "read AGENTS.md"
        )
        second = run_once(
            model, tools, atlas.config, session["id"], "read AGENTS.md again"
        )

        assert result == "read ok"
        assert second == "read ok"
        assert len(Handler.requests) == 4
        first_tools = {
            item["function"]["name"] for item in Handler.requests[0]["tools"]
        }
        assert len(first_tools) == 12
        assert any(
            message.get("role") == "tool"
            for message in Handler.requests[1]["messages"]
        )
    finally:
        if atlas is not None:
            atlas.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
