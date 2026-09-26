"""Which files in a repo Raven looks at. One place, shared by the digest,
search and symbols tools, so they agree.

- Respects .gitignore when the repo is a git checkout (`git ls-files`),
  otherwise walks the tree, pruning dependency/build/cache directories
  instead of descending into them (node_modules alone can be 100k files).
- Never surfaces secret files (.env, private keys): anything the model
  reads is sent to the model provider."""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", "node_modules", ".raven",
    "dist", "build", "target", ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache",
    "coverage", "htmlcov", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    ".idea", ".vscode", ".claude",
}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
SECRET_PATTERNS = ("*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "*.keystore")
SECRET_ALLOWED = {".env.example", ".env.sample", ".env.template"}


def is_secret_path(path: str) -> bool:
    name = Path(path).name
    if name in SECRET_ALLOWED:
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    return any(fnmatch.fnmatch(name, pat) for pat in SECRET_PATTERNS)


def _wanted(rel: str) -> bool:
    parts = Path(rel).parts
    if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in parts[:-1]):
        return False
    return parts[-1] not in SKIP_FILES and not is_secret_path(rel)


def _git_files(repo_root: Path) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    # only trust git's view if this directory is the top of the checkout
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=str(repo_root), capture_output=True, text=True,
    ).stdout.strip()
    if not top or Path(top).resolve() != repo_root:
        return None
    return proc.stdout.splitlines()


def list_repo_files(repo_root: Path) -> list[str]:
    """Repo-relative paths of the files worth looking at, sorted."""
    repo_root = repo_root.resolve()
    rels = _git_files(repo_root)
    if rels is None:
        rels = []
        for dirpath, dirnames, filenames in os.walk(repo_root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
            base = Path(dirpath).relative_to(repo_root)
            rels.extend(str(base / f) if str(base) != "." else f for f in filenames)
    return sorted(r for r in rels if _wanted(r) and (repo_root / r).is_file())


def exclude_raven_dir(repo_root: Path) -> None:
    """Keep Raven's own state (<repo>/.raven: reports, caches, memory) out
    of the target repo's diff via .git/info/exclude (plan §9.3) -- the
    working tree must hold only the final patch. Best-effort, idempotent."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            return  # not a git repo -- nothing to exclude from
        exclude = Path(proc.stdout.strip())
        if not exclude.is_absolute():
            exclude = Path(repo_root) / exclude
        existing = exclude.read_text() if exclude.exists() else ""
        if ".raven/" in existing.splitlines():
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as f:
            f.write(("" if existing.endswith("\n") or not existing else "\n") + ".raven/\n")
    except (OSError, subprocess.SubprocessError):
        pass
