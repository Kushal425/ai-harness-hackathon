"""Sub-agent base helper (plan §7.3): a one-shot model call with a distinct
system prompt, returning a small structured JSON handoff — never the full
transcript. This is the isolation mechanism: a sub-agent only ever sees
what's explicitly passed as `task_text` here, not the orchestrator's own
working history, avoiding both context pollution and (for the Reviewer)
self-grading bias."""

from __future__ import annotations

from raven.core.json_utils import parse_json_block
from raven.llm.protocol import Message


def run_subagent(gateway, system_prompt: str, task_text: str, response_tag: str, max_retries: int = 1) -> dict:
    """Returns the parsed ```<response_tag> {...}``` block, or {} if it
    couldn't be parsed even after retrying — callers must treat an empty
    dict as "no usable answer" and degrade gracefully, never raise."""
    messages = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=task_text),
    ]
    for attempt in range(max_retries + 1):
        try:
            completion = gateway.complete(messages)
            return parse_json_block(completion.text, response_tag)
        except Exception:
            if attempt >= max_retries:
                return {}
    return {}
