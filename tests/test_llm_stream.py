from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
        data = b"".join(
            f"data: {json.dumps(chunk)}\n\n".encode("utf-8") for chunk in chunks
        )
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
