"""Shared message/response types for the LLM gateway. Kept provider-agnostic
so real and fake clients speak the same shape."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class CompletionResult:
    text: str
    usage: Usage = field(default_factory=Usage)
    raw: dict = field(default_factory=dict)
