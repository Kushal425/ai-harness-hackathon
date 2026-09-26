"""OpenAI-compatible chat-completions client over httpx. This is the only
provider Raven speaks; any endpoint implementing the OpenAI chat-completions
shape (the organiser's endpoint included) works through it."""

from __future__ import annotations

import json
from typing import Callable, Iterable

import httpx

from raven.llm.protocol import CompletionResult, Message, Usage


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0,
        seed: int = 7,
        max_output_tokens: int = 2048,
        timeout_s: int = 60,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.max_output_tokens = max_output_tokens
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )

    def _payload(self, messages: Iterable[Message], stream: bool) -> dict:
        return {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
            "seed": self.seed,
            "max_tokens": self.max_output_tokens,
            "stream": stream,
        }

    def complete(
        self,
        messages: list[Message],
        stream: bool = False,
        on_token: Callable[[str], None] | None = None,
    ) -> CompletionResult:
        if stream and on_token is not None:
            return self._complete_streaming(messages, on_token)
        resp = self._client.post("/chat/completions", json=self._payload(messages, stream=False))
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
        usage_raw = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
        )
        return CompletionResult(text=text, usage=usage, raw=data)

    def _complete_streaming(
        self, messages: list[Message], on_token: Callable[[str], None]
    ) -> CompletionResult:
        chunks: list[str] = []
        with self._client.stream(
            "POST", "/chat/completions", json=self._payload(messages, stream=True)
        ) as resp:
            if resp.status_code >= 400:
                # Must read the body before it's accessible on the raised
                # exception — httpx streaming responses aren't buffered
                # until .read() is called, and exc.response.text on an
                # unread stream raises its own (misleading) error.
                resp.read()
                resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                event = json.loads(payload)
                delta = event["choices"][0].get("delta", {})
                token = delta.get("content")
                if token:
                    chunks.append(token)
                    on_token(token)
        return CompletionResult(text="".join(chunks))

    def close(self) -> None:
        self._client.close()
