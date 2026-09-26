"""The LLM Gateway (plan §4.1 layer 11): retries with seeded exponential
backoff, token accounting, and a one-shot capability probe used at startup.
Robustness beats features — every failure here degrades gracefully rather
than crashing the run (plan §0.4)."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from raven.llm.protocol import CompletionResult, Message, Usage


@dataclass
class GatewayStats:
    calls: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMGateway:
    """Wraps any client exposing `.complete(messages, stream, on_token)` with
    retry/backoff and usage accounting. Client is OpenAICompatibleClient in
    production, FakeClient in tests."""

    def __init__(self, client, max_retries: int = 5, seed: int = 7):
        self.client = client
        self.max_retries = max_retries
        self._rng = random.Random(seed)
        self.stats = GatewayStats()

    def complete(
        self,
        messages: list[Message],
        stream: bool = False,
        on_token: Callable[[str], None] | None = None,
    ) -> CompletionResult:
        attempt = 0
        last_exc: Exception | None = None
        while attempt <= self.max_retries:
            try:
                self.stats.calls += 1
                result = self.client.complete(messages, stream=stream, on_token=on_token)
                self.stats.prompt_tokens += result.usage.prompt_tokens
                self.stats.completion_tokens += result.usage.completion_tokens
                return result
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code
                # 4xx other than 429 (rate limit) will never succeed on retry —
                # bad key, wrong model name, malformed request. Fail fast with
                # the server's own error body instead of burning 5 retries.
                if status != 429 and status < 500:
                    raise RuntimeError(self._describe(exc)) from exc
                attempt += 1
                self.stats.retries += 1
                if attempt > self.max_retries:
                    break
                time.sleep(min(2 ** attempt, 30) + self._rng.uniform(0, 1))
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                attempt += 1
                self.stats.retries += 1
                if attempt > self.max_retries:
                    break
                time.sleep(min(2 ** attempt, 30) + self._rng.uniform(0, 1))
        raise RuntimeError(
            f"LLM gateway: exhausted {self.max_retries} retries — {self._describe(last_exc)}"
        ) from last_exc

    @staticmethod
    def _describe(exc: Exception | None) -> str:
        if exc is None:
            return "unknown error"
        if isinstance(exc, httpx.HTTPStatusError):
            body = exc.response.text.strip()
            return f"HTTP {exc.response.status_code} from {exc.request.url}: {body[:300]}"
        return f"{type(exc).__name__}: {exc}"

    def probe_tool_calling(self) -> bool:
        """One tiny call to check whether the endpoint supports native tool
        calling. Failures here just mean "assume no" — never crash startup."""
        try:
            probe_messages = [
                Message(role="system", content="Reply with the single word: ok"),
                Message(role="user", content="ping"),
            ]
            result = self.client.complete(probe_messages, stream=False)
            return bool(result.text)
        except Exception:
            return False

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()
