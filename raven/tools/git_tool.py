"""git status/diff only — no commits, no push (plan §8.2, §8.5). Degrades
gracefully when the target isn't a git repo (eval scratch copies aren't).

# Bug caught during Phase 2 (behind the Reviewer sub-agent, plan §7.3):
# `git -C <dir> diff/status` happily walks *up* to an ancestor .git if
# <dir> itself isn't a repo root — e.g. an eval scratch copy that lives
# under this project's own working tree. That silently reports the
# *outer* repo's uncommitted changes instead of "not a git repo", which
# badly confused the Reviewer (it reviewed this whole Phase 2 diff instead
# of the toy task's one-line fix). is_repo_toplevel() guards against it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from raven.tools.registry import RunContext, Tool, ToolResult


def _run_git(repo_root, args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo_root), capture_output=True, text=True, timeout=15
    )


def is_repo_toplevel(repo_root) -> bool:
    """True only if repo_root is itself the root of its own git working
    tree — not merely nested somewhere inside a larger one."""
    proc = _run_git(repo_root, ["rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        return False
    try:
        return Path(proc.stdout.strip()).resolve() == Path(repo_root).resolve()
    except OSError:
        return False


def status(ctx: RunContext) -> ToolResult:
    if not is_repo_toplevel(ctx.repo_root):
        return ToolResult(ok=True, output="(not a git repository)")
    proc = _run_git(ctx.repo_root, ["status", "--porcelain"])
    if proc.returncode != 0:
        return ToolResult(ok=True, output="(not a git repository)")
    return ToolResult(ok=True, output=proc.stdout.strip() or "(clean)")


def diff(ctx: RunContext) -> ToolResult:
    if not is_repo_toplevel(ctx.repo_root):
        return ToolResult(ok=True, output="(not a git repository)")
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
