from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .config import ConfigError
from .curator import CuratorJob
from .llm import ModelError
from .orchestrator import Atlas

MAX_BODY_BYTES = 10_000_000


class AtlasRPC:
    """Small JSON-RPC facade used by the future VSCode extension.

    Methods are intentionally one-shot and synchronous. Streaming/SSE is a
    later milestone; the CLI remains the reference client.
    """

    def __init__(self, root: str):
        self.root = root
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _session_lock(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.Lock())

    def dispatch(self, method: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        atlas = Atlas(self.root)
        try:
            if method == "doctor":
                return atlas.doctor()
            if method == "session.create":
                return atlas.create_session(params.get("title"))
            if method == "session.get":
                session_id = params.get("session_id")
                if not session_id:
                    raise ValueError("session_id is required")
                return atlas.session(session_id)
            if method == "session.list":
                return atlas.list_sessions(int(params.get("limit", 20)))
            if method == "chat.send":
                session_id = params.get("session_id")
                message = params.get("message")
                if not session_id or not message:
                    raise ValueError("session_id and message are required")
                # One turn per session at a time: concurrent HTTP requests used
                # to interleave turns/checkpoints and duplicate seq values.
                with self._session_lock(str(session_id)):
                    return {"content": atlas.chat(str(session_id), str(message))}
            if method == "knowledge.search":
                hits = atlas.knowledge_search(params.get("query", ""), int(params.get("limit", 8)))
                return [
                    {
                        "id": h.item.id,
                        "kind": h.item.kind,
                        "scope": h.item.scope,
                        "title": h.item.title,
                        "snippet": h.snippet,
                    }
                    for h in hits
                ]
            if method == "knowledge.add":
                return {
                    "id": atlas.remember(
                        title=params.get("title", ""),
                        body=params.get("body", ""),
                        kind=params.get("kind", "fact"),
                        scope=params.get("scope", "project"),
                        source="rpc",
                    )
                }
            if method == "knowledge.validate":
                return {
                    "ok": atlas.knowledge.promote(
                        params.get("id", ""),
                        params.get("validation") or {"method": "human", "result": "passed"},
                        actor="human",
                    )
                }
            if method == "curator.run":
                return CuratorJob(atlas).run_once(
                    use_llm=params.get("llm") is True,
                    replay=params.get("replay") is True,
                )
            raise ValueError(f"unknown method: {method}")
        finally:
            atlas.close()


class _Handler(BaseHTTPRequestHandler):
    server_version = "AtlasRPC/0.1"
    rpc: AtlasRPC
    token: str | None

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # Keep the daemon quiet; the CLI/extension owns logging.
        return

    def setup(self) -> None:
        super().setup()
        # Bound slow/partial clients; model work itself is not socket-bound.
        try:
            self.connection.settimeout(60)
        except OSError:
            pass

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not self.token:
            return True
        header = self.headers.get("Authorization", "")
        return hmac.compare_digest(header, f"Bearer {self.token}")

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/health":
            self._send(404, {"error": "not found"})
            return
        self._send(200, {"ok": True, "service": "atlas", "version": 1})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/rpc":
            self._send(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        request_id: Any = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                self._send(413, {"error": "request body too large"})
                return
            raw = self.rfile.read(length) if length else b"{}"
            request = json.loads(raw.decode("utf-8"))
            request_id = request.get("id")
            if request.get("jsonrpc") not in (None, "2.0"):
                self._send(
                    400,
                    {"jsonrpc": "2.0", "id": request_id, "error": "unsupported jsonrpc version"},
                )
                return
            method = request.get("method")
            if not method:
                self._send(400, {"jsonrpc": "2.0", "id": request_id, "error": "method is required"})
                return
            params = request.get("params") or {}
            if not isinstance(params, dict):
                self._send(
                    400, {"jsonrpc": "2.0", "id": request_id, "error": "params must be an object"}
                )
                return
            result = self.rpc.dispatch(method, params)
            self._send(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
        except (ConfigError, ModelError, ValueError, OSError) as exc:
            self._send(200, {"jsonrpc": "2.0", "id": request_id, "error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive boundary
            self._send(
                500,
                {"jsonrpc": "2.0", "id": request_id, "error": f"{type(exc).__name__}: {exc}"},
            )


def serve(
    root: str = ".", host: str = "127.0.0.1", port: int = 8765, token: str | None = None
) -> None:
    token = token or os.environ.get("ATLAS_TOKEN")
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not loopback and not token:
        raise ConfigError(
            "refusing to expose the Atlas daemon on a non-loopback address without "
            "authentication; set ATLAS_TOKEN or pass --token"
        )
    rpc = AtlasRPC(root)
    handler = type("AtlasHandler", (_Handler,), {"rpc": rpc, "token": token})
    server = ThreadingHTTPServer((host, port), handler)
    print(
        f"atlas rpc listening on http://{host}:{server.server_address[1]} (POST /rpc, GET /health)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
