"""Project memory (plan §9.3, §12.2): durable, cross-run facts about a
repository -- test command, architecture notes -- written to
<repo_root>/.raven/memory/project.md. Append-only and idempotent: a fact
already recorded (exact text match) is never duplicated. This is meant for
a handful of durable facts, not a searchable corpus -- retrieval elsewhere
(lessons.py) does the ranked/fuzzy matching."""

from __future__ import annotations

from pathlib import Path

_HEADER = "# Project memory\n\n"


def project_memory_path(repo_root: Path) -> Path:
    return Path(repo_root) / ".raven" / "memory" / "project.md"


def record_fact(repo_root: Path, fact: str) -> bool:
    """Returns True if `fact` was newly appended, False if already present."""
    path = project_memory_path(repo_root)
    existing = path.read_text() if path.exists() else _HEADER
    if fact in existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing + f"- {fact}\n")
    return True


def read_project_memory(repo_root: Path) -> str:
    path = project_memory_path(repo_root)
    return path.read_text() if path.exists() else ""
