"""Restricted command execution (plan §8.5): one allowlisted command, run
WITHOUT a shell (no chaining, pipes, redirection or substitution), path
arguments confined to the repo, find's destructive actions refused,
secrets scrubbed from the environment, and the whole process group killed
on timeout. policy.py decides allow/deny before dispatch.

Not a sandbox for the repo's own code: `python script.py` / pytest run the
target's code with normal OS permissions (as any test runner does)."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from raven.repo.interpreter import target_python
from raven.tools.policy import ShellCommandError, parse_shell_command
from raven.tools.registry import RunContext, Tool, ToolResult

SECRET_MARKERS = ("KEY", "TOKEN", "SECRET")


def _scrubbed_env() -> dict:
    return {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in SECRET_MARKERS)}


def target_code_env(repo_root=None) -> dict:
    """Environment for ANY subprocess that runs the target repo's code
    (tests, tracer, coverage): secrets scrubbed (plan §8.5 -- the repo's
    tests must never see AI_API_KEY), no bytecode written into the repo,
    and the repo importable."""
    env = _scrubbed_env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if repo_root is not None:
        env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
    return env


FIND_DENY = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}


def _outside_repo(arg: str, repo_root: Path) -> bool:
    """A path-looking argument that resolves outside the repo."""
    value = arg.split("=", 1)[1] if arg.startswith("-") and "=" in arg else arg
    if arg.startswith("-") and "=" not in arg:
        return False
    if not (value.startswith(("/", "~", "..")) or "/.." in value):
        return False
    resolved = (repo_root / Path(value).expanduser()).resolve()
    return resolved != repo_root and repo_root not in resolved.parents


def build_argv(cmd: str, repo_root: Path) -> list[str]:
    """Validated argv for one allowlisted command (raises ShellCommandError).
    python / pytest run with the TARGET repo's interpreter."""
    tokens = parse_shell_command(cmd)
    name = Path(tokens[0]).name
    for arg in tokens[1:]:
        if _outside_repo(arg, repo_root):
            raise ShellCommandError(f"path outside the repository: {arg}")
    if name == "find" and FIND_DENY & set(tokens):
        raise ShellCommandError(f"find actions {sorted(FIND_DENY & set(tokens))} are not allowed")
    if name in ("python", "python3"):
        rest = tokens[1:]
        if rest[:2] == ["-m", "pytest"]:
            pass
        elif not rest or rest[0].startswith("-"):
            raise ShellCommandError("python may only run a script in the repo or `-m pytest` (no -c / other -m)")
        return [target_python(repo_root)] + rest
    if name == "pytest":
        return [target_python(repo_root), "-m", "pytest"] + tokens[1:]
    return tokens


def run(ctx: RunContext, cmd: str, timeout: int = 30) -> ToolResult:
    repo_root = ctx.repo_root.resolve()
    try:
        argv = build_argv(cmd, repo_root)
    except ShellCommandError as exc:
        return ToolResult(ok=False, output=f"command refused: {exc}")
    try:
        # no shell: nothing in `cmd` is ever interpreted as shell syntax;
        # own process group, so a timeout kills everything it spawned
        proc = subprocess.Popen(
            argv, cwd=str(repo_root), env=target_code_env(repo_root),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
    except OSError as exc:
        return ToolResult(ok=False, output=f"could not run {argv[0]}: {exc}")
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.communicate()
        return ToolResult(ok=False, output=f"command timed out after {timeout}s and was killed: {cmd}")

    return ToolResult(ok=proc.returncode == 0, output=(output or "").strip() or "(no output)",
                      data={"returncode": proc.returncode})


SHELL_TOOL = Tool(
    name="shell",
    description="Run ONE allowlisted command (python <script>, python -m pytest, pytest, ls, cat, grep, "
    "echo, pwd, find) in the repo root. No shell: no pipes, &&, ;, redirection. Paths must stay in the repo.",
    params={"cmd": "str", "timeout": "int?"},
    permission="write",
    fn=run,
)
