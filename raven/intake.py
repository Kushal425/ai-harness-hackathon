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
    "  /repo /path/to/repo               (inside the TUI; a git URL is cloned)"
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
    """Clones `url` (once) into dest_root/<name> and returns the path.
    Raises RuntimeError with git's message on failure."""
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


def fetch_github_issue(owner: str, repo: str, number: str, timeout: float = 10.0) -> str | None:
    """Title + body of a public GitHub issue, or None on any failure."""
    try:
        import httpx

        headers = {"Accept": "application/vnd.github+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{number}",
            headers=headers, timeout=timeout, follow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        title = (data.get("title") or "").strip()
        body = (data.get("body") or "").strip()
        if not title and not body:
            return None
        return f"{title}\n\n{body}".strip()
    except Exception:
        return None


def expand_issue_refs(text: str, fetch=fetch_github_issue) -> str:
    """Appends the fetched title/body of every GitHub issue URL in `text`.
    Unfetchable issues are left as-is (the URL still reaches the model)."""
    extra = []
    for owner, repo, number in dict.fromkeys(GITHUB_ISSUE_RE.findall(text)):
        issue = fetch(owner, repo, number)
        if issue:
            extra.append(f"--- GitHub issue {owner}/{repo}#{number} ---\n{issue}")
    return text if not extra else text.rstrip() + "\n\n" + "\n\n".join(extra)


def ask_for_target_repo(ask, say) -> str | None:
    """Interactive start inside Raven's own tree: ask which repo to work
    on. `ask(prompt) -> str`, `say(text)`. Returns the answer, or None to
    stay where we are."""
    say("Raven is running inside its own source tree. Which repository should it work on?\n"
        "Enter a path or a git URL (it will be cloned), or press Enter to stay here.")
    try:
        answer = ask("repo path or git URL: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return answer or None
