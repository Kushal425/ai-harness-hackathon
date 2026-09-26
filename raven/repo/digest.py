"""Repo digest (plan §8.3): a cheap, cached snapshot built once per run —
file tree, a Python symbol index, detected test command, a language/stack
guess, and the opening of the README. No reference graph, convention
detector or ranked repo map yet."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from raven.repo.files import list_repo_files
from raven.tools.symbols import _defs_in_file
from raven.tools.test_runner import detect_test_command

LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go",
    ".java": "java", ".kt": "kotlin",
    ".rs": "rust",
    ".rb": "ruby", ".php": "php", ".cs": "csharp", ".c": "c", ".cpp": "cpp", ".swift": "swift",
}
# package.json dependencies worth naming in the stack line
JS_FRAMEWORKS = ("next", "react", "vue", "svelte", "@angular/core", "express", "vite", "tailwindcss")
README_NAMES = ("README.md", "README.rst", "README.txt", "README")
README_LINES = 25
# listed in file_count but kept out of the tree preview the model sees
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svg", ".mp4", ".mp3", ".wav",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pdf", ".zip", ".gz",
}


@dataclass
class RepoDigest:
    file_tree: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    test_command: str | None = None
    language: str | None = None
    file_count: int = 0
    stack: list[str] = field(default_factory=list)
    readme: str = ""

    def summary(self, max_files: int = 60, max_symbols: int = 30) -> str:
        # shallow paths first, so top-level files and main source dirs
        # show up before deeply nested ones when the tree is truncated
        ordered = sorted(
            (p for p in self.file_tree if Path(p).suffix.lower() not in BINARY_EXTENSIONS),
            key=lambda p: (p.count("/"), p),
        )
        tree_preview = "\n".join(ordered[:max_files])
        if len(ordered) > max_files:
            tree_preview += f"\n... ({len(ordered) - max_files} more files)"
        stack = f"  ·  stack: {', '.join(self.stack)}" if self.stack else ""
        parts = [
            f"language: {self.language or 'unknown'}{stack}  ·  files: {self.file_count}  ·  "
            f"test command: {self.test_command or 'none detected'}",
        ]
        if self.readme:
            parts.append(f"README (opening):\n{self.readme}")
        parts.append(f"file tree (partial):\n{tree_preview}")
        if self.symbols:
            parts.append("top symbols:\n" + "\n".join(self.symbols[:max_symbols]))
        return "\n".join(parts)


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
    # v2: digest format gained stack/readme and a new file filter
    return repo_root / ".raven" / "cache" / f"digest_v2_{_head_sha(repo_root)}.json"


def _detect_stack(repo_root: Path) -> list[str]:
    try:
        pkg = json.loads((repo_root / "package.json").read_text())
    except (OSError, ValueError):
        return []
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    return [name.split("/")[-1] if name.startswith("@") else name for name in JS_FRAMEWORKS if name in deps]


def _readme_head(repo_root: Path) -> str:
    for name in README_NAMES:
        path = repo_root / name
        if path.is_file():
            try:
                lines = [line.rstrip() for line in path.read_text(errors="replace").splitlines()]
            except OSError:
                return ""
            # skip blank lines and badge/image-only lines
            text = [line for line in lines if line.strip() and not line.lstrip().startswith(("![", "[![", "<img", "<p align"))]
            return "\n".join(text[:README_LINES])
    return ""


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

    for rel in list_repo_files(repo_root):
        path = repo_root / rel
        file_tree.append(rel)
        if path.suffix in LANGUAGE_EXTENSIONS:
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
        stack=_detect_stack(repo_root),
        readme=_readme_head(repo_root),
    )

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(asdict(digest)))
    except OSError:
        pass  # caching is best-effort; never fail the run over it

    return digest
