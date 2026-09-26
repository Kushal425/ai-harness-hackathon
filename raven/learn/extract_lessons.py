"""Cross-task lesson extraction (plan §12.2): at the end of every run, one
model call turns the run's outcome into a structured, durable lesson,
stored via raven/memory/lessons.py for retrieval on future runs. Never
raises -- a broken extraction must not affect the run's own accepted/reason
verdict, which is already decided by the time this runs; it only affects
what future runs get to see."""

from __future__ import annotations

from pathlib import Path

from raven.core.json_utils import parse_json_block
from raven.llm.protocol import Message
from raven.memory.lessons import GLOBAL_LESSONS_PATH, Lesson, add_lesson, repo_lessons_path
from raven.prompts import get_prompt

LESSON_EXTRACTION_PROMPT = get_prompt("lesson_extraction_prompt")

VALID_SCOPES = {"repo", "global"}


def extract_and_store_lesson(gateway, repo_root: Path, goal: str, executor_result, verdict, evidence: dict | None) -> dict | None:
    outcome_text = (
        f"Goal: {goal}\n"
        f"Outcome: {'RESOLVED' if verdict.accepted else 'UNRESOLVED'}\n"
        f"Reason: {verdict.reason}\n"
        f"Aborted reason: {executor_result.aborted_reason or '(none)'}\n"
        f"Evidence: {evidence}\n"
    )
    try:
        completion = gateway.complete(
            [Message(role="system", content=LESSON_EXTRACTION_PROMPT), Message(role="user", content=outcome_text)]
        )
        data = parse_json_block(completion.text, "lesson")
    except Exception:
        return None

    trigger = str(data.get("trigger", "")).strip()
    insight = str(data.get("insight", "")).strip()
    if not trigger or not insight:
        return None  # model judged nothing generalizable happened

    scope = data.get("scope") if data.get("scope") in VALID_SCOPES else "repo"
    try:
        confidence = float(data.get("confidence", 0.5) or 0.5)
    except (TypeError, ValueError):
        confidence = 0.5

    lesson = Lesson(
        trigger=trigger, insight=insight, action=str(data.get("action", "")).strip(),
        scope=scope, confidence=confidence,
    )
    store_path = repo_lessons_path(repo_root) if scope == "repo" else GLOBAL_LESSONS_PATH
    add_lesson(store_path, lesson)
    return data
