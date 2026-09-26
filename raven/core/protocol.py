"""Text tool-calling protocol (plan §8.1's text fallback): the model emits
one fenced ```action {json} ``` block per turn. Parsing is tolerant — it
finds the last such block and repairs common JSON mistakes (trailing
commas, smart quotes) before giving up."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

ACTION_BLOCK_RE = re.compile(r"```action\s*(.*?)```", re.DOTALL)


@dataclass
class ParsedAction:
    tool: str
    args: dict


class ActionParseError(Exception):
    pass


def _repair_json(raw: str) -> str:
    repaired = raw.strip()
    repaired = repaired.replace("“", '"').replace("”", '"')
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)  # trailing commas
    return repaired


def parse_action(text: str) -> ParsedAction:
    matches = ACTION_BLOCK_RE.findall(text)
    if not matches:
        raise ActionParseError("no ```action block found in model output")

    raw = matches[-1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = json.loads(_repair_json(raw))
        except json.JSONDecodeError as exc:
            raise ActionParseError(f"malformed JSON in action block: {exc}") from exc

    if not isinstance(data, dict) or "tool" not in data:
        raise ActionParseError("action block must be a JSON object with a 'tool' key")

    return ParsedAction(tool=data["tool"], args=data.get("args", {}) or {})


FORMAT_REMINDER = (
    "Your last reply could not be parsed. End your reply with exactly one "
    "```action\\n{\"tool\": \"...\", \"args\": {...}}\\n``` block, valid JSON, "
    "no trailing commas."
)
