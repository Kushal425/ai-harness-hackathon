"""Context assembly (plan §9.1): every model call's input is built fresh
from current state, not accumulated as a growing chat log. Order matches
the plan: system/tool docs, task card, plan, digest, then decaying working
history."""

from __future__ import annotations

from dataclasses import dataclass, field

from raven.context.decay import HistoryEntry, apply_decay
from raven.llm.protocol import Message
from raven.prompts import get_prompt

# Text lives in prompts/base.yaml (plan §12.3).
PROTOCOL_INSTRUCTIONS = get_prompt("protocol_instructions")
ANSWER_MODE_INSTRUCTIONS = get_prompt("answer_mode_instructions")
REPRODUCE_INSTRUCTIONS = get_prompt("reproduce_instructions")


@dataclass
class ContextState:
    task_card: str
    digest_summary: str = ""
    plan_text: str = ""
    history: list[HistoryEntry] = field(default_factory=list)
    half_life: int = 3
    answer_mode: bool = False
    # Pinned lessons (plan §12.1 in-run reflections + §12.2 retrieved
    # cross-task lessons), rendered every turn alongside the digest --
    # small and high-signal, so never subject to decay like history is.
    run_lessons: list[str] = field(default_factory=list)
    # plan §11.2: ask the model to write a failing reproduction test first
    reproduce: bool = False


class ContextEngine:
    def __init__(self, tool_docs: str, half_life: int = 3):
        self.tool_docs = tool_docs
        self.half_life = half_life

    def assemble(self, state: ContextState, current_turn: int) -> list[Message]:
        system_text = f"{PROTOCOL_INSTRUCTIONS}\nAvailable tools:\n{self.tool_docs}"
        if state.answer_mode:
            system_text += f"\n{ANSWER_MODE_INSTRUCTIONS}"
        elif state.reproduce:
            system_text += f"\n{REPRODUCE_INSTRUCTIONS}"

        sections = [f"# Task\n{state.task_card}"]
        if state.plan_text:
            sections.append(f"# Plan\n{state.plan_text}")
        if state.digest_summary:
            sections.append(f"# Repository digest\n{state.digest_summary}")
        if state.run_lessons:
            sections.append("# Lessons\n" + "\n".join(f"- {l}" for l in state.run_lessons))

        decayed = apply_decay(state.history, current_turn, self.half_life)
        if decayed:
            sections.append("# Working history\n" + "\n---\n".join(decayed))

        sections.append("Respond with your next action.")
        user_text = "\n\n".join(sections)

        return [
            Message(role="system", content=system_text),
            Message(role="user", content=user_text),
        ]
