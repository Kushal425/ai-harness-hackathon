"""Which Python runs the TARGET repository's code (plan §8.3: "the target
interpreter — the repo's own environment, not our venv").

Raven's own venv has Raven's dependencies, not the target's: running the
target's tests with it makes every test that imports a third-party package
ERROR, before and after the fix alike, which silently destroys the
evidence. Candidates, first match wins, each only if it can import pytest:
  1. $RAVEN_TARGET_PYTHON
  2. the repo's own virtualenv (.venv/, venv/, env/)
  3. python3 / python on PATH (the evaluation environment's interpreter)
  4. Raven's own interpreter (last resort: stdlib-only repos still work)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

VENV_DIRS = (".venv", "venv", "env")


def _has_pytest(python: str) -> bool:
    try:
        proc = subprocess.run(
            [python, "-c", "import pytest"], capture_output=True, timeout=20,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _candidates(repo_root: Path) -> list[str]:
    out = []
    override = os.environ.get("RAVEN_TARGET_PYTHON")
    if override:
        out.append(override)
    for d in VENV_DIRS:
        for rel in ("bin/python", "Scripts/python.exe"):
            p = repo_root / d / rel
            if p.exists():
                out.append(str(p))
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            out.append(found)
    return list(dict.fromkeys(out))


@lru_cache(maxsize=32)
def _resolve(repo_root: str) -> str:
    for python in _candidates(Path(repo_root)):
        if _has_pytest(python):
            return python
    return sys.executable


def target_python(repo_root: Path) -> str:
    """The interpreter to run `repo_root`'s tests and code with (cached)."""
    return _resolve(str(Path(repo_root).resolve()))
