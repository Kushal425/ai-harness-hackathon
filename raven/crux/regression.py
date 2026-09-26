"""Targeted regression checks: only the test files that import a touched
module (a test map from imports, not the whole suite), run once on the
original code for a baseline and once per surviving candidate."""

from __future__ import annotations

import subprocess
from pathlib import Path

from raven.crux.repomap import RepoMap, module_name
from raven.repo.interpreter import target_python
from raven.tools.shell import target_code_env
from raven.tools.test_runner import FAILURE_RE


def related_tests(rmap: RepoMap, touched: list[str], cap: int = 12) -> list[str]:
    mods = {module_name(p) for p in touched}
    tops = {m.split(".")[0] for m in mods} | {Path(p).stem for p in touched}
    hits, weak = [], []
    for test in rmap.test_files:
        if not Path(test).name.startswith("test") and not test.endswith("_test.py"):
            continue
        imported = rmap.imports.get(test, set())
        if any(m == i or i.startswith(m + ".") or m.startswith(i + ".") for m in mods for i in imported):
            hits.append(test)
        elif any(i.split(".")[0] in tops for i in imported):
            weak.append(test)
    return (hits or weak)[:cap]


def run_tests(repo_root: Path, tests: list[str], timeout: int = 180) -> tuple[bool, list[str], str]:
    """(ran_ok, failed_ids, tail). ran_ok=False when pytest itself couldn't run."""
    if not tests:
        return True, [], ""
    repo_root = Path(repo_root).resolve()
    cmd = [target_python(repo_root), "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests]
    try:
        proc = subprocess.run(cmd, cwd=str(repo_root), env=target_code_env(repo_root),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, [], f"tests timed out after {timeout}s"
    out = proc.stdout + proc.stderr
    failed = FAILURE_RE.findall(out)
    ran = proc.returncode in (0, 1)
    return ran, failed, "\n".join(out.strip().splitlines()[-15:])
