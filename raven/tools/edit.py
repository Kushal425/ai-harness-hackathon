"""Exact or whitespace-normalized search/replace edit, with a syntax check
and auto-revert on failure (plan §8.2, §10.1)."""

from __future__ import annotations

import py_compile
import re
import tempfile
from pathlib import Path

from raven.tools.registry import RunContext, Tool, ToolResult


def _normalize(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def _find_normalized(original: str, search: str) -> int | None:
    """Best-effort: locate `search` in `original` ignoring run-length of
    whitespace. Returns the char offset of the match start, or None."""
    target = _normalize(search)
    original_lines = original.splitlines(keepends=True)
    search_line_count = len(search.splitlines()) or 1
    for i in range(len(original_lines) - search_line_count + 1):
        window = "".join(original_lines[i : i + search_line_count])
        if _normalize(window) == target:
            return sum(len(l) for l in original_lines[:i])
    return None


def edit(ctx: RunContext, path: str, search: str, replace: str) -> ToolResult:
    resolved = (ctx.repo_root / path).resolve()
    if not resolved.exists():
        return ToolResult(ok=False, output=f"no such file: {path}")

    original = resolved.read_text()

    if search in original:
        new_text = original.replace(search, replace, 1)
    else:
        offset = _find_normalized(original, search)
        if offset is None:
            closest_line = _closest_match_hint(original, search)
            return ToolResult(
                ok=False,
                output=f"edit failed: SEARCH text not found in {path}.{closest_line}",
            )
        search_line_count = len(search.splitlines()) or 1
        lines = original.splitlines(keepends=True)
        # recompute start line index from offset
        running = 0
        start_idx = 0
        for idx, line in enumerate(lines):
            if running == offset:
                start_idx = idx
                break
            running += len(line)
        new_text = "".join(lines[:start_idx]) + replace + "".join(lines[start_idx + search_line_count :])

    resolved.write_text(new_text)

    if path.endswith(".py"):
        ok, err = _check_syntax(resolved)
        if not ok:
            resolved.write_text(original)  # auto-revert
            return ToolResult(ok=False, output=f"edit reverted: syntax error after edit: {err}")

    return ToolResult(ok=True, output=f"edited {path}")


def _check_syntax(path: Path) -> tuple[bool, str]:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            py_compile.compile(str(path), cfile=str(Path(tmp) / "out.pyc"), doraise=True)
        return True, ""
    except py_compile.PyCompileError as exc:
        return False, str(exc)


def _closest_match_hint(original: str, search: str) -> str:
    target = _normalize(search)[:40]
    for lineno, line in enumerate(original.splitlines(), start=1):
        if target[:15] and target[:15] in _normalize(line):
            return f" closest match near line {lineno} differs in whitespace/content."
    return ""


EDIT_TOOL = Tool(
    name="edit",
    description="Replace the first occurrence of `search` with `replace` in a file. "
    "Falls back to whitespace-normalized matching. Reverts automatically if the "
    "result doesn't parse as valid Python.",
    params={"path": "str", "search": "str", "replace": "str"},
    permission="write",
    fn=edit,
)
