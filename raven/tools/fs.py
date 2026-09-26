"""Filesystem tools: read and create, both jailed to the repo root."""

from __future__ import annotations

from pathlib import Path

from raven.tools.registry import RunContext, Tool, ToolResult


def _resolve_in_repo(ctx: RunContext, path: str) -> Path | None:
    candidate = (ctx.repo_root / path).resolve()
    repo_root = ctx.repo_root.resolve()
    if candidate == repo_root or repo_root in candidate.parents:
        return candidate
    return None


def read(ctx: RunContext, path: str, start: int | None = None, end: int | None = None) -> ToolResult:
    resolved = _resolve_in_repo(ctx, path)
    if resolved is None:
        return ToolResult(ok=False, output=f"path escapes repo root: {path}")
    if not resolved.exists():
        return ToolResult(ok=False, output=f"no such file: {path}")
    if not resolved.is_file():
        return ToolResult(ok=False, output=f"not a file: {path}")

    lines = resolved.read_text(errors="replace").splitlines()
    lo = (start or 1) - 1
    hi = end if end is not None else len(lines)
    lo = max(0, lo)
    hi = min(len(lines), hi)
    numbered = "\n".join(f"{i + 1:>5}  {line}" for i, line in enumerate(lines[lo:hi], start=lo))
    return ToolResult(ok=True, output=numbered, data={"path": path, "start": lo + 1, "end": hi})


def create(ctx: RunContext, path: str, content: str) -> ToolResult:
    resolved = _resolve_in_repo(ctx, path)
    if resolved is None:
        return ToolResult(ok=False, output=f"path escapes repo root: {path}")
    if resolved.exists():
        return ToolResult(ok=False, output=f"refusing to overwrite existing file: {path}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content)
    return ToolResult(ok=True, output=f"created {path} ({len(content.splitlines())} lines)")


READ_TOOL = Tool(
    name="read",
    description="Read a file, optionally a line range. Returns line-numbered text.",
    params={"path": "str", "start": "int?", "end": "int?"},
    permission="read",
    fn=read,
)

CREATE_TOOL = Tool(
    name="create",
    description="Create a new file inside the repo. Never overwrites an existing file.",
    params={"path": "str", "content": "str"},
    permission="write",
    fn=create,
)
