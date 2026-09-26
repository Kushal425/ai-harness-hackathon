"""Context assembly (plan §9.1): every model call's input is built fresh
from current state, not accumulated as a growing chat log. Order matches
the plan: system/tool docs, task card, plan, digest, then decaying working
history."""

from __future__ import annotations

from dataclasses import dataclass, field

from raven.context.decay import HistoryEntry, apply_decay
from raven.llm.protocol import Message

PROTOCOL_INSTRUCTIONS = """\
You are Raven, an autonomous coding agent. You work by emitting exactly one
tool action per turn, as a fenced block at the end of your reply:

```action
{"tool": "<tool name>", "args": {...}}
```

Think briefly in plain text before the block if useful, but always end your
reply with exactly one ```action fenced block. When the task is complete,
call the `done` tool: {"tool": "done", "args": {"summary": "what you did"}}.
"""


@dataclass
class ContextState:
    task_card: str
    digest_summary: str = ""
    plan_text: str = ""
    history: list[HistoryEntry] = field(default_factory=list)
    half_life: int = 3


class ContextEngine:
    def __init__(self, tool_docs: str, half_life: int = 3):
        self.tool_docs = tool_docs
        self.half_life = half_life

    def assemble(self, state: ContextState, current_turn: int) -> list[Message]:
        system_text = f"{PROTOCOL_INSTRUCTIONS}\nAvailable tools:\n{self.tool_docs}"

        sections = [f"# Task\n{state.task_card}"]
        if state.plan_text:
            sections.append(f"# Plan\n{state.plan_text}")
        if state.digest_summary:
            sections.append(f"# Repository digest\n{state.digest_summary}")

        decayed = apply_decay(state.history, current_turn, self.half_life)
        if decayed:
            sections.append("# Working history\n" + "\n---\n".join(decayed))

        sections.append("Respond with your next action.")
        user_text = "\n\n".join(sections)

        return [
            Message(role="system", content=system_text),
            Message(role="user", content=user_text),
        ]
