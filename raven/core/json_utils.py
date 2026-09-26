"""Shared tolerant JSON-in-fenced-block parsing, used by the action protocol
and by the one-shot Understanding/Planner model calls."""

from __future__ import annotations

import json
import re

FENCE_RE_CACHE: dict[str, re.Pattern] = {}


def _fence_re(tag: str) -> re.Pattern:
    if tag not in FENCE_RE_CACHE:
        FENCE_RE_CACHE[tag] = re.compile(rf"```{tag}\s*(.*?)```", re.DOTALL)
    return FENCE_RE_CACHE[tag]


def extract_fenced(text: str, tag: str) -> str | None:
    matches = _fence_re(tag).findall(text)
    return matches[-1] if matches else None


def repair_json(raw: str) -> str:
    repaired = raw.strip()
    repaired = repaired.replace("“", '"').replace("”", '"')
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)  # trailing commas
    return repaired


def parse_json_block(text: str, tag: str) -> dict:
    raw = extract_fenced(text, tag)
    if raw is None:
        raise ValueError(f"no ```{tag} block found")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(repair_json(raw))
