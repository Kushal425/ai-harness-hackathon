"""Permission policy (plan §8.5). Mode-based resolution. Phase 1 collapsed
every "ask" cell in the plan's table to a static default because there was
no approval channel to ask through. Phase 2 adds one: an optional
`approve_fn(description, args) -> bool` threaded in via RunContext
(raven/tools/registry.py) and wired up by the TUI (raven/ui/tui.py). When
`approve_fn` is None — every non-interactive path: autonomous CLI runs, the
eval harness, the plain REPL — behaviour is byte-for-byte the Phase 1
static default. Only an interactive caller that actually supplies
`approve_fn` gets real prompts. Editing existing test files stays denied in
every mode regardless of approval; that protection doesn't depend on having
a human to ask.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

from raven.recovery.checkpoints import is_scratch_path

READ_TOOLS = {"read", "search", "symbols", "outline", "tests", "git_status", "git_diff"}
SHELL_ALLOWLIST = {"python", "python3", "pytest", "ls", "cat", "grep", "echo", "pwd", "find"}

ApproveFn = Callable[[str, dict], bool]


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


def _is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    return "/test" in p or p.startswith("test") or "_test.py" in p or p.startswith("tests/")


def _ask(approve_fn: ApproveFn | None, description: str, args: dict, default: PolicyDecision) -> PolicyDecision:
    """Resolves an "ask" cell: real approval if a channel is wired up,
    else the Phase 1 static default (never blocks a non-interactive run)."""
    if approve_fn is None:
        return default
    try:
        return PolicyDecision.ALLOW if approve_fn(description, args) else PolicyDecision.DENY
    except Exception:
        return default  # a broken approval channel must never crash the run


def check_policy(
    mode: str, tool_name: str, permission: str, args: dict, approve_fn: ApproveFn | None = None
) -> PolicyDecision:
    if tool_name in READ_TOOLS:
        return PolicyDecision.ALLOW

    if mode == "chat":
        return PolicyDecision.DENY  # chat is read-only by design (plan §3)

    if tool_name in ("edit", "create"):
        path = args.get("path", "")
        # Only *editing* an existing test file is protected — creating a new
        # test file is how test_writing tasks and feature tasks add coverage,
        # and `create` already refuses to overwrite anything that exists.
        # Raven's own scratch area (.raven/, e.g. the reproduction test) is
        # never part of the patch, so its test files aren't protected.
        if tool_name == "edit" and _is_test_path(path) and not is_scratch_path(path):
            return PolicyDecision.DENY
        if mode == "plan":
            # plan mode previews, never writes silently — ask if there's a
            # channel to preview through, else the Phase 1 default (deny).
            return _ask(approve_fn, f"apply this {tool_name} to {path}?", args, PolicyDecision.DENY)
        return PolicyDecision.ALLOW  # act / autonomous

    if tool_name == "shell":
        cmd = (args.get("cmd") or "").strip()
        first_token = cmd.split(" ")[0] if cmd else ""
        if first_token in SHELL_ALLOWLIST:
            if mode in ("act", "autonomous"):
                return PolicyDecision.ALLOW
            return _ask(approve_fn, f"run shell command: {cmd}?", args, PolicyDecision.DENY)
        if mode == "act":
            return _ask(approve_fn, f"run non-allowlisted shell command: {cmd}?", args, PolicyDecision.DENY)
        return PolicyDecision.DENY  # autonomous / chat / plan: always denied

    return PolicyDecision.DENY
