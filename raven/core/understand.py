"""Task Understanding (plan §7.1): one model call producing a small JSON
task card. A deterministic regex pre-pass extracts candidate files/errors
from the goal text and is merged in, so the harness isn't solely dependent
on the model noticing them. Degrades to sane defaults if the model's JSON
is unusable — never raises."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from raven.core.json_utils import parse_json_block
from raven.llm.gateway import LLMGateway
from raven.llm.protocol import Message

VALID_TASK_TYPES = {
    "bug_fix", "feature", "refactor", "test_writing", "question",
    "review", "performance", "docs", "config",
}
VALID_VERIFICATION = {"repro_test", "new_tests", "existing_tests", "static_only", "answer_only"}

UNDERSTAND_SYSTEM_PROMPT = """\
You are Raven's task-understanding step. Read the task goal and respond with
exactly one fenced block:

```understanding
{
  "task_type": "bug_fix | feature | refactor | test_writing | question | review | performance | docs | config",
  "summary": "one sentence",
  "entities": {"files": [], "symbols": [], "errors": [], "commands": []},
  "acceptance_criteria": ["observable, checkable statements"],
  "verification_strategy": "repro_test | new_tests | existing_tests | static_only | answer_only",
  "ambiguities": [{"question": "...", "default_assumption": "..."}],
  "risk": "low | medium | high"
}
```
"""


@dataclass
class Understanding:
    task_type: str = "bug_fix"
    summary: str = ""
    entities: dict = field(default_factory=dict)
    acceptance_criteria: list[str] = field(default_factory=list)
    verification_strategy: str = "existing_tests"
    ambiguities: list[dict] = field(default_factory=list)
    risk: str = "medium"


def _pre_pass_entities(goal: str) -> dict:
    files = re.findall(r"[\w./\-]+\.(?:py|js|ts|go|java|rs)", goal)
    errors = re.findall(r"\b\w*Error\b|\b\w*Exception\b", goal)
    commands = re.findall(r"`([^`]+)`", goal)
    return {
        "files": sorted(set(files)),
        "errors": sorted(set(errors)),
        "commands": commands,
    }


def understand(gateway: LLMGateway, goal: str) -> Understanding:
    pre_pass = _pre_pass_entities(goal)
    messages = [
        Message(role="system", content=UNDERSTAND_SYSTEM_PROMPT),
        Message(role="user", content=goal),
    ]

    try:
        completion = gateway.complete(messages)
        data = parse_json_block(completion.text, "understanding")
    except Exception:
        data = {}

    task_type = data.get("task_type") if data.get("task_type") in VALID_TASK_TYPES else "bug_fix"
    verification = (
        data.get("verification_strategy")
        if data.get("verification_strategy") in VALID_VERIFICATION
        else "existing_tests"
    )
    entities = data.get("entities") or {}
    for key, values in pre_pass.items():
        merged = set(entities.get(key, [])) | set(values)
        entities[key] = sorted(merged)

    return Understanding(
        task_type=task_type,
        summary=data.get("summary") or goal[:200],
        entities=entities,
        acceptance_criteria=data.get("acceptance_criteria") or [],
        verification_strategy=verification,
        ambiguities=data.get("ambiguities") or [],
        risk=data.get("risk") or "medium",
    )
