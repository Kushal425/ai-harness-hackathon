"""File-level checkpoints (plan §10.2): snapshot a file's bytes before any
write, so a failed or aborted run can always be restored to exactly the
state it started in. This is what guarantees "clean tree" in Phase 1 —
not a git operation, just in-memory/on-disk content snapshots, so it works
even when the target isn't a git repo (eval scratch copies aren't)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

SCRATCH_DIR = ".raven"


def is_scratch_path(path: str) -> bool:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p == SCRATCH_DIR or p.startswith(SCRATCH_DIR + "/")


@dataclass
class Checkpoint:
    path: str
    existed: bool
    content: str | None
    timestamp: float = field(default_factory=time.time)


class CheckpointManager:
    """One instance per run. Call snapshot(path) before every write; call
    restore_to_clean() to undo everything taken this run, in reverse order."""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root)
        self._checkpoints: list[Checkpoint] = []
        self._seen_paths: set[str] = set()

    def snapshot(self, path: str) -> None:
        if path in self._seen_paths:
            return  # only the first pre-write state matters for a clean restore
        resolved = self.repo_root / path
        existed = resolved.exists()
        content = resolved.read_text(errors="replace") if existed else None
        self._checkpoints.append(Checkpoint(path=path, existed=existed, content=content))
        self._seen_paths.add(path)

    def restore_to_clean(self) -> list[str]:
        """Reverts every snapshot taken this run, most recent first. Returns
        the list of paths restored."""
        restored = []
        for cp in reversed(self._checkpoints):
            resolved = self.repo_root / cp.path
            if cp.existed:
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_text(cp.content)
            elif resolved.exists():
                resolved.unlink()
            restored.append(cp.path)
        self._checkpoints.clear()
        self._seen_paths.clear()
        return restored

    @property
    def touched_paths(self) -> list[str]:
        """Paths the run changed, i.e. the patch. Raven's own scratch files
        (.raven/, e.g. the reproduction test) are snapshotted for a clean
        restore but are not part of the patch."""
        return [cp.path for cp in self._checkpoints if not is_scratch_path(cp.path)]

    def original_content(self, path: str) -> str | None:
        """The pre-write snapshot for `path`, or None if it didn't exist
        before this run (or was never touched). Used by
        raven/verify/behavior_diff.py to diff pre/post function behavior
        without re-reading from disk after the edit already landed."""
        for cp in self._checkpoints:
            if cp.path == path:
                return cp.content
        return None

    def has_changes(self) -> bool:
        return bool(self._checkpoints)
