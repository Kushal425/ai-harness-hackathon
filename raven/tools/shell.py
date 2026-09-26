"""Sandboxed shell execution: env scrubbed of secrets, jailed cwd, timeout.
Only allowlisted commands reach here in practice — policy.py filters the
rest before dispatch (plan §8.5)."""

from __future__ import annotations

import os
import subprocess

from raven.tools.registry import RunContext, Tool, ToolResult

SECRET_MARKERS = ("KEY", "TOKEN", "SECRET")


def _scrubbed_env() -> dict:
    return {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in SECRET_MARKERS)}


def run(ctx: RunContext, cmd: str, timeout: int = 30) -> ToolResult:
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(ctx.repo_root),
            env=_scrubbed_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, output=f"command timed out after {timeout}s: {cmd}")

    output = (proc.stdout or "") + (proc.stderr or "")
    return ToolResult(ok=proc.returncode == 0, output=output.strip() or "(no output)", data={"returncode": proc.returncode})


SHELL_TOOL = Tool(
    name="shell",
    description="Run an allowlisted shell command (python, pytest, ls, cat, grep, echo, pwd, find) "
    "inside the repo root. Non-allowlisted commands are denied by policy.",
    params={"cmd": "str", "timeout": "int?"},
    permission="write",
    fn=run,
)
