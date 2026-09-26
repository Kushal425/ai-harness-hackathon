"""In-run reflection (plan §12.1): after a failed step, one short model
call asks what assumption turned out false and what to do differently. The
answer becomes a pinned "run lesson" for the rest of THIS run only -- it is
not persisted (that's raven/learn/extract_lessons.py's job, once at the end
of the run). Capped at REFLECTION_CAP calls per run so a run that keeps
failing doesn't spend its whole budget reflecting instead of acting."""

from __future__ import annotations

from dataclasses import dataclass, field

from raven.llm.protocol import Message
from raven.prompts import get_prompt

REFLECTION_CAP = 3

REFLECT_PROMPT = get_prompt("reflect_prompt")


def reflect_on_failure(gateway, failure_context: str) -> str:
    """Never raises -- a broken reflection call degrades to an empty
    string, which the caller simply doesn't pin."""
    try:
        completion = gateway.complete(
            [Message(role="system", content=REFLECT_PROMPT), Message(role="user", content=failure_context)]
        )
        return completion.text.strip()
    except Exception:
        return ""


@dataclass
class RunReflection:
    """One instance per orchestrator run. `lessons` grows as failures
    happen; pass `pinned + reflection.lessons` into each run_single_loop
    call so later plan steps see what was learned from earlier ones."""

    cap: int = REFLECTION_CAP
    used: int = 0
    lessons: list[str] = field(default_factory=list)

    def maybe_reflect(self, gateway, failure_context: str) -> None:
        if self.used >= self.cap or not failure_context:
            return
        insight = reflect_on_failure(gateway, failure_context)
        self.used += 1
        if insight:
            self.lessons.append(insight)
