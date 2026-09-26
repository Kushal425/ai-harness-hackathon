"""Planner (plan §7.2): one model call producing at most 7 steps, each with
an action and a check. Falls back to a single catch-all step if the model's
JSON is unusable — the plan_execute path must never hard-fail here."""

from __future__ import annotations

from dataclasses import dataclass, field

from raven.core.json_utils import parse_json_block
from raven.core.understand import Understanding
from raven.llm.gateway import LLMGateway
from raven.llm.protocol import Message
from raven.prompts import get_prompt

MAX_STEPS = 7

# Text lives in prompts/base.yaml (plan §12.3).
PLAN_SYSTEM_PROMPT = get_prompt("plan_system")


@dataclass
class PlanStep:
    id: int
    action: str
    tools: list[str] = field(default_factory=list)
    check: str = ""
    status: str = "pending"  # pending | done | failed


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)

    def as_text(self) -> str:
        marks = {"pending": "·", "done": "✓", "failed": "✗"}
        return "\n".join(
            f"{marks[s.status]} {s.id} {s.action} (check: {s.check})" for s in self.steps
        )


def make_plan(gateway: LLMGateway, understanding: Understanding, digest_summary: str, note: str = "") -> Plan:
    task_card = (
        f"task_type: {understanding.task_type}\nsummary: {understanding.summary}\n"
        f"acceptance_criteria: {understanding.acceptance_criteria}\n"
        f"repo digest: {digest_summary}"
    )
    if note:
        task_card += f"\n\nNote from a previous attempt: {note}"

    messages = [
        Message(role="system", content=PLAN_SYSTEM_PROMPT),
        Message(role="user", content=task_card),
    ]

    steps_raw = []
    try:
        completion = gateway.complete(messages)
        data = parse_json_block(completion.text, "plan")
        steps_raw = data.get("steps") or []
    except Exception:
        pass

    if not steps_raw:
        steps_raw = [
            {
                "action": understanding.summary or "resolve the task",
                "tools": ["read", "search", "edit", "tests"],
                "check": "tests pass",
            }
        ]

    steps = [
        PlanStep(
            id=i + 1,
            action=str(s.get("action", "")),
            tools=list(s.get("tools", [])),
            check=str(s.get("check", "")),
        )
        for i, s in enumerate(steps_raw[:MAX_STEPS])
    ]
    return Plan(steps=steps)
