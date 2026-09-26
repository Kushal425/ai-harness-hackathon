"""Test-running tool: auto-detects pytest, runs it, parses a compact
pass/fail summary and failure signatures (plan §8.2, §8.4). Named
test_runner (not tests.py) so it never collides with pytest collection."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from raven.tools.registry import RunContext, Tool, ToolResult


def detect_test_command(repo_root: Path) -> str | None:
    markers = ["pytest.ini", "pyproject.toml", "setup.cfg", "tests", "test"]
    if any((repo_root / m).exists() for m in markers):
        return "pytest"
    return None


def run_tests(ctx: RunContext, target: str | None = None, timeout: int = 60) -> ToolResult:
    repo_root = ctx.repo_root.resolve()
    if detect_test_command(repo_root) is None:
        return ToolResult(ok=False, output="no test command detected for this repo")

    cmd = [sys.executable, "-m", "pytest", "-q"]
    if target:
        cmd.append(target)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")

    try:
        proc = subprocess.run(
            cmd, cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, output=f"tests timed out after {timeout}s")

    output = proc.stdout + proc.stderr
    passed = proc.returncode == 0
    tail = "\n".join(output.strip().splitlines()[-40:])
    return ToolResult(
        ok=passed,
        output=tail,
        data={"returncode": proc.returncode, "passed": passed},
    )


TESTS_TOOL = Tool(
    name="tests",
    description="Run the repo's test suite (pytest), optionally scoped to one file/test id. "
    "Returns pass/fail and the trimmed failure output.",
    params={"target": "str?", "timeout": "int?"},
    permission="read",  # runs code but doesn't modify the repo
    fn=run_tests,
)
