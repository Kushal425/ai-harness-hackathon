"""git status/diff only — no commits, no push (plan §8.2, §8.5). Degrades
gracefully when the target isn't a git repo (eval scratch copies aren't)."""

from __future__ import annotations

import subprocess

from raven.tools.registry import RunContext, Tool, ToolResult


def _run_git(repo_root, args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo_root), capture_output=True, text=True, timeout=15
    )


def status(ctx: RunContext) -> ToolResult:
    proc = _run_git(ctx.repo_root, ["status", "--porcelain"])
    if proc.returncode != 0:
        return ToolResult(ok=True, output="(not a git repository)")
    return ToolResult(ok=True, output=proc.stdout.strip() or "(clean)")


def diff(ctx: RunContext) -> ToolResult:
    proc = _run_git(ctx.repo_root, ["diff"])
    if proc.returncode != 0:
        return ToolResult(ok=True, output="(not a git repository)")
    return ToolResult(ok=True, output=proc.stdout.strip() or "(no changes)")


STATUS_TOOL = Tool(
    name="git_status",
    description="Show `git status --porcelain` for the repo.",
    params={},
    permission="read",
    fn=status,
)

DIFF_TOOL = Tool(
    name="git_diff",
    description="Show `git diff` for the repo's working tree.",
    params={},
    permission="read",
    fn=diff,
)
