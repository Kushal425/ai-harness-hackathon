"""Test-running tool: auto-detects pytest, runs it, parses a compact
pass/fail summary and failure signatures (plan §8.2, §8.4). Named
test_runner (not tests.py) so it never collides with pytest collection."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from raven.repo.interpreter import target_python
from raven.tools.registry import RunContext, Tool, ToolResult
from raven.tools.shell import target_code_env

# pytest's short summary: FAILED for assertion failures, ERROR for tests that
# couldn't even run (a broken import, a fixture error). Both are failures --
# counting only FAILED would make an edit that breaks the package's import
# look like it "fixed" every test it broke.
FAILURE_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


def detect_test_command(repo_root: Path) -> str | None:
    markers = ["pytest.ini", "pyproject.toml", "setup.cfg", "tests", "test"]
    if any((repo_root / m).exists() for m in markers):
        return "pytest"
    return None


def run_tests(ctx: RunContext, target: str | None = None, timeout: int = 60) -> ToolResult:
    repo_root = ctx.repo_root.resolve()
    if detect_test_command(repo_root) is None:
        return ToolResult(ok=False, output="no test command detected for this repo")

    # -p no:cacheprovider + PYTHONDONTWRITEBYTECODE: running the tests must
    # not leave .pytest_cache/ or __pycache__/ behind in the target repo
    python = target_python(repo_root)  # the repo's own environment, not Raven's venv
    cmd = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if target:
        cmd.append(target)

    env = target_code_env(repo_root)

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
        # parsed from the FULL output -- the model only sees the tail, but
        # the verifier's pre/post comparison must see every failure
        data={
            "returncode": proc.returncode, "passed": passed, "failed": FAILURE_RE.findall(output),
            "python": python,
        },
    )


TESTS_TOOL = Tool(
    name="tests",
    description="Run the repo's test suite (pytest), optionally scoped to one file/test id. "
    "Returns pass/fail and the trimmed failure output.",
    params={"target": "str?", "timeout": "int?"},
    permission="read",  # runs code but doesn't modify the repo
    fn=run_tests,
)
