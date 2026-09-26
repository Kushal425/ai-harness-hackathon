"""Age-based decay of working-history entries (plan §9.2). Pure function,
unit-testable independent of the context engine: older tool results shrink
to a one-line stub once they're more than `half_life` turns old."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HistoryEntry:
    turn: int
    role: str  # "action" | "result"
    text: str
    path: str | None = None  # for result entries, the file/tool target


def shrink(entry: HistoryEntry, current_turn: int, half_life: int) -> str:
    age = current_turn - entry.turn
    if age <= half_life:
        return entry.text
    line_count = entry.text.count("\n") + 1
    target = f" {entry.path}" if entry.path else ""
    return f"[older result{target}, {line_count} lines omitted (age {age} turns)]"


def apply_decay(entries: list[HistoryEntry], current_turn: int, half_life: int) -> list[str]:
    return [shrink(e, current_turn, half_life) for e in entries]
