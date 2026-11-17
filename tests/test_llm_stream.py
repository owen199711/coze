from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from atlas.config import AtlasConfig, ConfigError
from atlas.llm import OpenAICompatibleClient


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        assert body.get("stream") is True
        chunks = [
            {
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "content": "hello ",
                        }
                    }
                ]
            },
            {"choices": [{"delta": {"content": "world"}}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read",
                                        "arguments": '{"path": ',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '"README.md"}'},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 6},
            },
        ]
        data = b"".join(f"data: {json.dumps(chunk)}\n\n".encode("utf-8") for chunk in chunks)
        data += b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


def test_chat_stream_aggregates_text_and_tool_fragments(monkeypatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model = OpenAICompatibleClient(
            api_base=f"http://127.0.0.1:{server.server_address[1]}",
            api_key="test",
            model="fake",
        )
        seen: list[str] = []
        reply = model.chat_stream(
            [{"role": "user", "content": "read it"}],
            tools=[{"type": "function", "function": {"name": "read"}}],
            on_text=seen.append,
        )
        assert "".join(seen) == "hello world"
        assert reply.content == "hello world"
        assert reply.tool_calls == [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "read",
                    "arguments": '{"path": "README.md"}',
                },
            }
        ]
        assert reply.input_tokens == 4
        assert reply.output_tokens == 6
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class FlakyHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        type(self).calls += 1
        if type(self).calls == 1:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(
            {
                "choices": [{"message": {"content": "recovered"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def test_transient_5xx_is_retried() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FlakyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        model = OpenAICompatibleClient(
            api_base=f"http://127.0.0.1:{server.server_address[1]}",
            api_key="test",
            model="fake",
            max_retries=1,
            retry_backoff=0.0,
        )
        reply = model.chat([{"role": "user", "content": "hi"}])
        assert reply.content == "recovered"
        assert FlakyHandler.calls == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_from_config_rejects_lookalike_localhost_without_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ATLAS_TEST_MISSING_KEY", raising=False)
    config = AtlasConfig(
        root=tmp_path,
        model_name="fake",
        api_base="http://127.0.0.1.evil.example/v1",
        api_key_env="ATLAS_TEST_MISSING_KEY",
    )
    with pytest.raises(ConfigError, match="missing API key"):
        OpenAICompatibleClient.from_config(config)
