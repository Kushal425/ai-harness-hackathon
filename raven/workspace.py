"""Isolated workspaces for repositories fetched from GitHub (or any git URL).

  workspaces/<owner>__<repo>/base          one clone per repository, fetched
                                           fresh on reuse; Raven never edits it
  workspaces/<owner>__<repo>/tasks/<id>    one git worktree per task, on its own
                                           branch raven/<id>, cut from the chosen
                                           remote branch -- all edits happen here

Nothing is ever pushed: the result of a task is the diff of its worktree
(also saved as a .patch next to the run report). The access token reaches
git only through environment config (http.extraHeader), so it is never
stored in .git/config, a remote URL, or visible in a process listing.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from raven.config import REPO_ROOT
from raven.github import WEB, valid_name

WORKSPACES_DIR = REPO_ROOT / "workspaces"


class WorkspaceError(RuntimeError):
    pass


def git_env(token: str | None = None, web: str = WEB) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG")}
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang on a credential prompt
    if token and web.startswith("https://"):
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update({"GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": f"http.{web.rstrip('/')}/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"})
    return env


def _git(args: list[str], cwd: Path | None = None, env: dict | None = None, timeout: int = 600) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, env=env or git_env(),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceError(f"git {args[0]} timed out") from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip().splitlines()
        raise WorkspaceError(f"git {args[0]} failed: {msg[-1] if msg else proc.returncode}")
    return proc.stdout


def valid_branch(branch: str) -> bool:
    if not branch or branch.startswith("-") or len(branch) > 200:
        return False
    return subprocess.run(["git", "check-ref-format", "--branch", branch], capture_output=True).returncode == 0


def ensure_clone(owner: str, name: str, token: str | None = None, web: str = WEB,
                 root: Path | None = None) -> Path:
    """The (fresh) base clone of owner/name."""
    if not (valid_name(owner) and valid_name(name)):
        raise WorkspaceError(f"invalid repository name: {owner}/{name}")
    base = Path(root or WORKSPACES_DIR) / f"{owner}__{name}" / "base"
    env = git_env(token, web)
    if (base / ".git").exists():
        _git(["fetch", "--prune", "--quiet", "origin"], cwd=base, env=env)
    else:
        base.parent.mkdir(parents=True, exist_ok=True)
        _git(["clone", "--quiet", "--no-tags", f"{web.rstrip('/')}/{owner}/{name}.git", str(base)], env=env)
    return base


def new_worktree(base: Path, branch: str, label: str) -> Path:
    """A fresh worktree for one task, on branch raven/<label>-<time>."""
    if not valid_branch(branch):
        raise WorkspaceError(f"invalid branch name: {branch!r}")
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")[:40] or "task"
    task_id = f"{label}-{time.strftime('%Y%m%d-%H%M%S')}"
    path = base.parent / "tasks" / task_id
    path.parent.mkdir(parents=True, exist_ok=True)
    ref = f"origin/{branch}"
    try:
        _git(["rev-parse", "--verify", "--quiet", ref], cwd=base)
    except WorkspaceError:
        raise WorkspaceError(f"branch {branch!r} not found on the remote") from None
    _git(["worktree", "add", "--quiet", "-b", f"raven/{task_id}", str(path), ref], cwd=base)
    return path


def remote_branches(base: Path) -> list[str]:
    out = _git(["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"], cwd=base)
    return sorted(b.split("/", 1)[1] for b in out.split() if "/" in b and not b.endswith("/HEAD"))


def default_branch(base: Path) -> str:
    try:
        ref = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=base).strip()
        return ref.split("/", 1)[1]
    except WorkspaceError:
        return "main"


def worktree_diff(path: Path) -> str:
    """All changes in the task worktree, new files included (Raven's own
    .raven/ state is excluded via .git/info/exclude)."""
    _git(["add", "--all", "--intent-to-add", "."], cwd=path)
    return _git(["diff", "--no-color"], cwd=path)


def remove_worktree(path: Path) -> None:
    base = path.parent.parent / "base"
    try:
        _git(["worktree", "remove", "--force", str(path)], cwd=base)
    except WorkspaceError:
        shutil.rmtree(path, ignore_errors=True)


def in_workspace(path: Path) -> bool:
    try:
        return WORKSPACES_DIR.resolve() in Path(path).resolve().parents
    except OSError:
        return False


def export_patch(worktree: Path, report_path) -> Path | None:
    """Save the task worktree's diff next to the run report (the result of
    a GitHub task is a patch, never a push). None if not a workspace."""
    if not in_workspace(worktree) or not report_path:
        return None
    patch_path = Path(report_path).parent / "patch.diff"
    patch_path.write_text(worktree_diff(Path(worktree)))
    return patch_path
