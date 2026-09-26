"""One-shot, structured model calls for Crux stages -- no tool loop. Each
call returns one JSON object (fenced or bare, tolerantly parsed), retries
once with a format reminder, and is booked against its stage so the report
can show exactly where tokens went."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from raven.core.json_utils import repair_json
from raven.llm.protocol import Message
from raven.prompts import get_prompt

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass
class StageCost:
    calls: int = 0
    tokens: int = 0


@dataclass
class Ledger:
    """Per-stage model usage for the run (the report's cost breakdown)."""
    stages: dict[str, StageCost] = field(default_factory=dict)

    def book(self, stage: str, tokens: int) -> None:
        cost = self.stages.setdefault(stage, StageCost())
        cost.calls += 1
        cost.tokens += tokens

    @property
    def calls(self) -> int:
        return sum(c.calls for c in self.stages.values())

    @property
    def tokens(self) -> int:
        return sum(c.tokens for c in self.stages.values())


def extract_json(text: str) -> dict | None:
    candidates = [m for m in _FENCE.findall(text or "")] + [text or ""]
    decoder = json.JSONDecoder()
    for chunk in candidates:
        for start in (m.start() for m in re.finditer(r"\{", chunk)):
            for attempt in (chunk[start:], repair_json(chunk[start:])):
                try:
                    obj, _ = decoder.raw_decode(attempt)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    return obj
    return None


def ask_json(gateway, prompt_name: str, user: str, ledger: Ledger, stage: str,
             temperature: float | None = None, required: tuple = ()) -> dict | None:
    """System prompt from prompts/base.yaml, then `user`. Returns the parsed
    object (with `required` keys) or None after one corrective retry."""
    messages = [Message("system", get_prompt(prompt_name)), Message("user", user)]
    for attempt in range(2):
        try:
            completion = gateway.complete(messages, temperature=temperature)
        except Exception:
            ledger.book(stage, 0)
            return None
        # this call's own usage (the gateway total is shared by parallel calls)
        ledger.book(stage, completion.usage.total_tokens)
        data = extract_json(completion.text)
        if data is not None and all(k in data for k in required):
            return data
        messages = messages + [
            Message("assistant", completion.text[:2000]),
            Message("user", "That was not a valid JSON object with keys "
                            f"{list(required)}. Reply with ONLY the JSON object."),
        ]
    return None
