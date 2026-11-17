from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from .config import AtlasConfig, ConfigError

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class ModelError(RuntimeError):
    pass


class MaxTurnsExceeded(ModelError):
    """Raised when a turn needs more LLM calls than `max_turns` allows.

    See docs/adr/ADR-0002-max-turns-semantics.md. Carries the partial
    conversation so a caller can resume without losing evidence.
    """

    def __init__(
        self,
        max_turns: int,
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            f"max_turns ({max_turns}) exceeded: the agent needed more than "
            f"{max_turns} model calls to finish this turn. Raise "
            "model.max_turns or resume with the returned messages."
        )
        self.max_turns = max_turns
        self.messages = list(messages or [])


@dataclass
class ModelReply:
    content: str
    tool_calls: list[dict[str, Any]]
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict[str, Any] | None = None


class OpenAICompatibleClient:
    """A tiny chat-completions client.

    Works with any provider exposing an OpenAI-compatible /chat/completions API
    (OpenAI, DashScope compatible mode, DeepSeek, vLLM, Ollama, ...).
    """

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        timeout: int = 120,
        temperature: float = 0.1,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
    ):
        if not model:
            raise ConfigError("model.name is empty; set it in atlas.yaml or ATLAS_MODEL")
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = max(0.0, float(retry_backoff))

    def _open(self, request: urllib.request.Request):
        """Open a request with bounded retries on transient provider errors."""
        last: ModelError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return urllib.request.urlopen(request, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:1000]
                last = ModelError(f"model HTTP {exc.code}: {body}")
                if exc.code in RETRYABLE_STATUS and attempt < self.max_retries:
                    time.sleep(self.retry_backoff * (2**attempt))
                    continue
                raise last from exc
            except Exception as exc:
                last = ModelError(f"model request failed: {exc}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * (2**attempt))
                    continue
                raise last from exc
        raise last or ModelError("model request failed")

    @classmethod
    def from_config(cls, config: AtlasConfig) -> "OpenAICompatibleClient":
        model = config.model_name or os.environ.get("ATLAS_MODEL", "")
        if not model:
            raise ConfigError("model.name is empty; set it in atlas.yaml or ATLAS_MODEL")
        api_key = os.environ.get(config.api_key_env, "")
        host = (urlparse(config.api_base).hostname or "").lower()
        local = host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")
        if not api_key and not local:
            raise ConfigError(
                f"missing API key: set {config.api_key_env}, or point api_base at a local endpoint"
            )
        return cls(
            api_base=config.api_base,
            api_key=api_key or "local",
            model=model,
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelReply:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        request = urllib.request.Request(
            f"{self.api_base}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with self._open(request) as response:
                data = json.loads(response.read().decode("utf-8"))
        except ModelError:
            raise
        except Exception as exc:
            raise ModelError(f"invalid model response: {exc}") from exc

        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise ModelError(f"unexpected model response: {data}") from exc

        usage = data.get("usage") or {}
        return ModelReply(
            content=message.get("content") or "",
            tool_calls=message.get("tool_calls") or [],
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            raw=data,
        )

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> ModelReply:
        """Call a streaming chat completion and aggregate the final reply.

        `on_text` receives content deltas as they arrive. Tool-call fragments are
        aggregated by index and returned in the normal `ModelReply` shape, so the
        agent loop does not need a separate code path.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        request = urllib.request.Request(
            f"{self.api_base}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        content_parts: list[str] = []
        tool_parts: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        last_data: dict[str, Any] | None = None
        try:
            with self._open(request) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    last_data = data
                    if data.get("usage"):
                        usage = data["usage"]
                    for choice in data.get("choices") or []:
                        delta = choice.get("delta") or choice.get("message") or {}
                        text = delta.get("content")
                        if isinstance(text, list):
                            text = "".join(
                                str(block.get("text") or "")
                                for block in text
                                if isinstance(block, dict)
                            )
                        if text:
                            content_parts.append(str(text))
                            if on_text is not None:
                                on_text(str(text))
                        for tool_call in delta.get("tool_calls") or []:
                            index = int(tool_call.get("index") or 0)
                            entry = tool_parts.setdefault(
                                index,
                                {
                                    "id": tool_call.get("id") or f"call_{index}",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                },
                            )
                            if tool_call.get("id"):
                                entry["id"] = tool_call["id"]
                            function = tool_call.get("function") or {}
                            if function.get("name"):
                                entry["function"]["name"] += str(function["name"])
                            if function.get("arguments"):
                                entry["function"]["arguments"] += str(function["arguments"])
        except ModelError:
            raise
        except Exception as exc:
            raise ModelError(f"model stream failed: {exc}") from exc

        return ModelReply(
            content="".join(content_parts),
            tool_calls=[tool_parts[index] for index in sorted(tool_parts)],
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            raw=last_data,
        )
