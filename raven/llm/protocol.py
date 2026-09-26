"""Shared message/response types for the LLM gateway. Kept provider-agnostic
so real and fake clients speak the same shape."""

from __future__ import annotations

import json
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


def tool_call_to_action_text(name: str, arguments) -> str:
    """A native tool call rendered as Raven's text action block, so native
    and text protocols share one parse/policy/dispatch path downstream.
    Models sometimes namespace tool names ("repo.search",
    "functions.read"); only the last segment is Raven's tool name."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}
    # gpt-oss sometimes wraps Raven's own action JSON in a call to a tool
    # named after the format ("json"): {"tool": "done", "args": {...}}.
    wrapped = "tool" in arguments or ("name" in arguments and ("args" in arguments or "arguments" in arguments))
    inner = arguments.get("tool") or arguments.get("name")
    if wrapped and isinstance(inner, str) and inner and set(arguments) <= {"tool", "name", "args", "arguments"}:
        name = inner
        arguments = arguments.get("args", arguments.get("arguments", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
    tool = (name or "").split(".")[-1].strip()
    return "```action\n" + json.dumps({"tool": tool, "args": arguments}) + "\n```"
