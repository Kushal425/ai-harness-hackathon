"""Repo digest (plan §8.3): a cheap, cached snapshot built once per run —
file tree, a Python symbol index, detected test command, and a language
guess. Deliberately minimal for Phase 1: no reference graph, no convention
detector, no ranked repo map. Those are Phase-2+ additions."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from raven.tools.search import SKIP_DIRS
from raven.tools.symbols import _defs_in_file
from raven.tools.test_runner import detect_test_command

LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
}


@dataclass
class RepoDigest:
    file_tree: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    test_command: str | None = None
    language: str | None = None
    file_count: int = 0

    def summary(self, max_files: int = 40, max_symbols: int = 30) -> str:
        tree_preview = "\n".join(self.file_tree[:max_files])
        if len(self.file_tree) > max_files:
            tree_preview += f"\n... ({len(self.file_tree) - max_files} more files)"
        sym_preview = "\n".join(self.symbols[:max_symbols])
        return (
            f"language: {self.language or 'unknown'}  ·  files: {self.file_count}  ·  "
            f"test command: {self.test_command or 'none detected'}\n"
            f"file tree (partial):\n{tree_preview}\n"
            f"top symbols:\n{sym_preview}"
        )


def _head_sha(repo_root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root), capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "notgit"


def _cache_path(repo_root: Path) -> Path:
    return repo_root / ".raven" / "cache" / f"digest_{_head_sha(repo_root)}.json"


def build_digest(repo_root: Path, use_cache: bool = True) -> RepoDigest:
    cache_path = _cache_path(repo_root)
    if use_cache and cache_path.exists():
        try:
            return RepoDigest(**json.loads(cache_path.read_text()))
        except (json.JSONDecodeError, TypeError, OSError):
            pass  # fall through and rebuild

    repo_root = repo_root.resolve()
    file_tree: list[str] = []
    ext_counts: dict[str, int] = {}
    symbol_lines: list[str] = []

    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = str(path.relative_to(repo_root))
        file_tree.append(rel)
        ext_counts[path.suffix] = ext_counts.get(path.suffix, 0) + 1
        if path.suffix == ".py":
            for kind, name, start, end in _defs_in_file(path)[:5]:
                symbol_lines.append(f"{kind} {name}  {rel}:{start}")

    language = None
    if ext_counts:
        top_ext = max(ext_counts, key=ext_counts.get)
        language = LANGUAGE_EXTENSIONS.get(top_ext)

    digest = RepoDigest(
        file_tree=file_tree,
        symbols=symbol_lines,
        test_command=detect_test_command(repo_root),
        language=language,
        file_count=len(file_tree),
    )

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(asdict(digest)))
    except OSError:
        pass  # caching is best-effort; never fail the run over it

    return digest
