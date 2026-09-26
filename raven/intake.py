"""Intake (plan §14 steps 1-2): which repository Raven works on, and what
the task text is.

The evaluator runs `make run` from inside Raven's own checkout, so "the
current directory" is Raven itself, never the repository the issue is
about. The target is resolved from, in order: --repo, RAVEN_REPO, the
current directory — and if that turns out to be Raven's own source tree,
the caller must ask (TUI) or stop with instructions (non-interactive)
rather than start editing the harness.

A GitHub issue URL in the task text is expanded with the issue's title
and body, fetched by the harness (not by the model, whose tools have no
network access). A git URL given as the repository is cloned.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from raven.config import REPO_ROOT

WORKSPACES_DIR = REPO_ROOT / "workspaces"  # gitignored; where repo URLs are cloned
GITHUB_ISSUE_RE = re.compile(r"https?://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)")
GIT_URL_RE = re.compile(r"^(https?://\S+?|git@[\w.-]+:\S+?)(\.git)?/?$")

REPO_HELP = (
    "Point Raven at the repository to work on, any one of:\n"
    "  make run ARGS=\"--repo /path/to/repo\"\n"
    "  export RAVEN_REPO=/path/to/repo   (then make run)\n"
    "  /repo /path/to/repo               (inside the TUI; a git URL is cloned)\n"
    "or give a GitHub issue URL as the task (the repository is fetched automatically):\n"
    "  make run ARGS=\"--issue https://github.com/owner/repo/issues/123\""
)


def is_harness_repo(path: Path) -> bool:
    """True if `path` is Raven's own source tree."""
    try:
        return Path(path).resolve() == REPO_ROOT.resolve()
    except OSError:
        return False


def resolve_repo(cli_repo: str | None) -> Path:
    """--repo, else RAVEN_REPO, else the current directory."""
    chosen = cli_repo or os.environ.get("RAVEN_REPO") or "."
    return Path(chosen).expanduser().resolve()


def looks_like_git_url(text: str) -> bool:
    text = text.strip()
    return bool(GIT_URL_RE.match(text)) and not Path(text).expanduser().exists()


def clone_repo(url: str, dest_root: Path = WORKSPACES_DIR, timeout: int = 300) -> Path:
    """A working copy for a repository URL. GitHub repositories get an
    isolated per-task worktree (raven/workspace.py); other git URLs are
    cloned once into dest_root/<name>. Raises RuntimeError on failure."""
    from raven.github import parse_repo

    try:
        owner, name = parse_repo(url)
    except ValueError:
        owner = name = None
    if owner:
        path, _ = github_workspace(owner, name, label="session",
                                   root=None if dest_root == WORKSPACES_DIR else dest_root)
        return path
    url = url.strip().rstrip("/")
    name = re.sub(r"\.git$", "", url.split("/")[-1].split(":")[-1]) or "repo"
    dest = Path(dest_root) / name
    if (dest / ".git").exists():
        return dest.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "clone", "--quiet", url, str(dest)], capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return dest.resolve()


def github_workspace(owner: str, name: str, branch: str | None = None, label: str = "task",
                     client=None, web: str | None = None, root: Path | None = None) -> tuple[Path, str]:
    """Fresh clone/fetch of owner/name and a new isolated worktree for one
    task. Returns (worktree, branch). Raises RuntimeError on failure."""
    from raven.github import GitHubClient, GitHubError
    from raven.workspace import WorkspaceError, default_branch, ensure_clone, new_worktree

    try:
        from raven import github, workspace

        client = client or GitHubClient(allow_anonymous=True)
        base = ensure_clone(owner, name, client.token, web or github.WEB, root or workspace.WORKSPACES_DIR)
        branch = branch or default_branch(base)
        return new_worktree(base, branch, label), branch
    except (GitHubError, WorkspaceError) as exc:
        raise RuntimeError(str(exc)) from exc


def github_issue_task(owner: str, name: str, number: int, branch: str | None = None, client=None,
                      web: str | None = None, root: Path | None = None) -> tuple[Path, str]:
    """GitHub issue -> (isolated worktree, task text for the harness)."""
    from raven.github import GitHubClient, GitHubError

    try:
        client = client or GitHubClient(allow_anonymous=True)
        issue = client.issue(owner, name, number)
    except GitHubError as exc:
        raise RuntimeError(str(exc)) from exc
    path, _ = github_workspace(owner, name, branch, label=f"issue-{number}", client=client, web=web, root=root)
    return path, issue.to_task()


def fetch_github_issue(owner: str, repo: str, number: str, timeout: float = 10.0) -> str | None:
    """A GitHub issue (title, labels, body, discussion, linked PRs) as task
    text, or None on any failure. Uses the GitHub login if there is one."""
    try:
        from raven.github import GitHubClient

        return GitHubClient(allow_anonymous=True).issue(owner, repo, int(number)).to_task()
    except Exception:
        return None


def expand_issue_refs(text: str, fetch=fetch_github_issue) -> str:
    """Appends the fetched title/body of every GitHub issue URL in `text`.
    Unfetchable issues are left as-is (the URL still reaches the model)."""
    extra = []
    for owner, repo, number in list(dict.fromkeys(GITHUB_ISSUE_RE.findall(text)))[:3]:
        issue = fetch(owner, repo, number)
        if issue:
            extra.append(issue if issue.startswith("The task is described") else
                         f"--- GitHub issue {owner}/{repo}#{number} ---\n{issue}")
    return text if not extra else text.rstrip() + "\n\n" + "\n\n".join(extra)


def ask_for_target_repo(ask, say) -> str | None:
    """Interactive start inside Raven's own tree: ask which repo to work
    on. `ask(prompt) -> str`, `say(text)`. Returns the answer, or None to
    stay where we are."""
    say("Raven is running inside its own source tree. Which repository should it work on?\n"
        "Enter a path, a GitHub repo (owner/name or URL; cloned into an isolated workspace),\n"
        "or press Enter and use /gh to browse your GitHub repositories.")
    try:
        answer = ask("repo path or git URL: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return answer or None
