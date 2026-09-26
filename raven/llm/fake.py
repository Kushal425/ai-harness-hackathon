"""FakeClient: an in-memory, scripted stand-in for OpenAICompatibleClient.
Used by offline unit/e2e tests (plan §19) so `make test` never needs a key
or network access."""

from __future__ import annotations

from typing import Callable

from raven.llm.protocol import CompletionResult, Message, Usage


class FakeClient:
    def __init__(self, scripted_responses: list[str] | None = None):
        self._responses = list(scripted_responses or ["Hello from FakeClient."])
        self.calls: list[list[Message]] = []

    def complete(
        self,
        messages: list[Message],
        stream: bool = False,
        on_token: Callable[[str], None] | None = None,
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> CompletionResult:
        self.calls.append(messages)
        text = self._responses.pop(0) if self._responses else "(FakeClient: no more scripted responses)"
        if stream and on_token is not None:
            for word in text.split(" "):
                on_token(word + " ")
        return CompletionResult(
            text=text,
            usage=Usage(prompt_tokens=sum(len(m.content) for m in messages) // 4, completion_tokens=len(text) // 4),
        )

    def close(self) -> None:
        pass
