"""Text tool-calling protocol (plan §8.1's text fallback): the model emits
one fenced ```action {json} ``` block per turn. Parsing is tolerant — it
finds the last such block and repairs common JSON mistakes (trailing
commas, smart quotes) before giving up. Small models often drop or
mislabel the fence, so as fallbacks it also accepts a tool call inside any
other fenced block (```json ...```), or a bare {"tool": ...} object."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

ACTION_BLOCK_RE = re.compile(r"```action\s*(.*?)```", re.DOTALL)
ANY_FENCE_RE = re.compile(r"```[\w-]*\s*(.*?)```", re.DOTALL)


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


def _load(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_repair_json(raw))


def _is_action(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("tool"), str)


def _bare_action(text: str) -> dict | None:
    """The last JSON object in free text that looks like a tool call."""
    decoder = json.JSONDecoder()
    found = None
    for start in (m.start() for m in re.finditer(r"\{", text)):
        for candidate in (text[start:], _repair_json(text[start:])):
            try:
                data, _ = decoder.raw_decode(candidate)
            except json.JSONDecodeError:
                continue
            if _is_action(data):
                found = data
            break
    return found


def parse_action(text: str) -> ParsedAction:
    matches = ACTION_BLOCK_RE.findall(text)
    if matches:
        try:
            data = _load(matches[-1])
        except json.JSONDecodeError as exc:
            raise ActionParseError(f"malformed JSON in action block: {exc}") from exc
        if not _is_action(data):
            raise ActionParseError("action block must be a JSON object with a 'tool' key")
    else:
        data = None
        for raw in reversed(ANY_FENCE_RE.findall(text)):
            try:
                candidate = _load(raw)
            except json.JSONDecodeError:
                continue
            if _is_action(candidate):
                data = candidate
                break
        if data is None:
            data = _bare_action(text)
        if data is None:
            raise ActionParseError("no ```action block found in model output")

    args = data.get("args", {}) or {}
    if not isinstance(args, dict):
        raise ActionParseError("'args' must be a JSON object")
    return ParsedAction(tool=data["tool"], args=args)


FORMAT_REMINDER = (
    "Your last reply could not be parsed. End your reply with exactly one "
    "```action\\n{\"tool\": \"...\", \"args\": {...}}\\n``` block, valid JSON, "
    "no trailing commas."
)
