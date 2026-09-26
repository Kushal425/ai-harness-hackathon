"""Ochiai spectrum-based fault localization (plan §11 step 3). Runs each
test in a given set under sys.settrace in a subprocess, records which
source lines each test covers and whether it passed, then ranks lines by:

    ochiai(line) = failed_covering(line) / sqrt(total_failed * total_covering(line))

Simple and correct on small fixtures like tests/fixtures/toy_repo — this is
not general-purpose coverage tooling, and every failure mode degrades to an
empty ranking rather than raising (it enriches the report, it never gates
the verdict)."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from raven.repo.interpreter import target_python
from raven.tools.shell import target_code_env

_COVERAGE_SCRIPT = r'''
import sys, json, importlib.util, os

repo_root = sys.argv[1]
targets = json.loads(sys.argv[2])  # list of "path::func"
sys.path.insert(0, repo_root)

def _clear_repo_modules():
    # Force each test to freshly exec repo-local modules — otherwise
    # Python's import cache means only the *first* test that imports a
    # module actually traces its top-level lines, badly skewing coverage.
    for mod_name in list(sys.modules):
        mod = sys.modules[mod_name]
        f = getattr(mod, "__file__", None)
        if f and os.path.abspath(f).startswith(repo_root):
            del sys.modules[mod_name]

results = {}
for i, target in enumerate(targets):
    _clear_repo_modules()
    path_str, func_name = target.split("::")
    path_str = os.path.join(repo_root, path_str)  # absolute: same coverage on every Python version
    covered = set()

    def tracer(frame, event, arg, _covered=covered):
        if event == "line" and repo_root in frame.f_code.co_filename:
            _covered.add(f"{frame.f_code.co_filename}:{frame.f_lineno}")
        return tracer

    passed = True
    try:
        spec = importlib.util.spec_from_file_location(f"_cov_{i}", path_str)
        module = importlib.util.module_from_spec(spec)
        sys.settrace(tracer)
        spec.loader.exec_module(module)
        getattr(module, func_name)()
    except Exception:
        passed = False
    finally:
        sys.settrace(None)

    results[target] = {"passed": passed, "covered": sorted(covered)}

print(json.dumps(results))
'''


def run_sbfl(repo_root: Path, test_targets: list[str], top_n: int = 5, timeout: int = 60) -> list[tuple[str, float]]:
    """Returns up to top_n (location, suspiciousness) pairs, most suspicious
    first. test_targets are "path::func" strings (the same format
    baseline.TestSummary.failed already produces from pytest output)."""
    if not test_targets:
        return []

    script_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(_COVERAGE_SCRIPT)
            script_path = f.name

        proc = subprocess.run(
            [target_python(repo_root), script_path, str(Path(repo_root).resolve()), json.dumps(test_targets)],
            cwd=str(repo_root), env=target_code_env(repo_root), capture_output=True, text=True, timeout=timeout,
        )
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return []
    finally:
        if script_path:
            Path(script_path).unlink(missing_ok=True)

    total_failed = sum(1 for r in data.values() if not r["passed"])
    if total_failed == 0:
        return []

    covering: dict = defaultdict(lambda: {"failed": 0, "total": 0})
    for r in data.values():
        for line in r["covered"]:
            covering[line]["total"] += 1
            if not r["passed"]:
                covering[line]["failed"] += 1

    scored = []
    for line, counts in covering.items():
        if counts["failed"] == 0:
            continue
        score = counts["failed"] / ((total_failed * counts["total"]) ** 0.5)
        scored.append((line, round(score, 3)))

    scored.sort(key=lambda kv: -kv[1])
    return scored[:top_n]
