"""Permission policy (plan §8.5). Static, mode-based resolution: Phase 1 has
no interactive approval UI yet (that lands with the TUI in Phase 2), so any
action the plan table marks "ask" resolves here to its safe default instead
of blocking — deny for shell-other, and allow-with-checkpoint for editing a
non-test file in Act/Autonomous. Editing existing test files stays denied in
every mode; that protection doesn't depend on having a human to ask.

# PLAN-DECISION: "ask" collapses to a static default because there is no
# approval channel to ask through in Phase 1. Revisit once the TUI wires up
# real approval prompts.
"""

from __future__ import annotations

from enum import Enum

READ_TOOLS = {"read", "search", "symbols", "outline", "tests", "git_status", "git_diff"}
SHELL_ALLOWLIST = {"python", "python3", "pytest", "ls", "cat", "grep", "echo", "pwd", "find"}


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


def _is_test_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    return "/test" in p or p.startswith("test") or "_test.py" in p or p.startswith("tests/")


def check_policy(mode: str, tool_name: str, permission: str, args: dict) -> PolicyDecision:
    if tool_name in READ_TOOLS:
        return PolicyDecision.ALLOW

    if mode == "chat":
        return PolicyDecision.DENY  # chat is read-only by design (plan §3)

    if tool_name in ("edit", "create"):
        path = args.get("path", "")
        # Only *editing* an existing test file is protected — creating a new
        # test file is how test_writing tasks and feature tasks add coverage,
        # and `create` already refuses to overwrite anything that exists.
        if tool_name == "edit" and _is_test_path(path):
            return PolicyDecision.DENY
        if mode == "plan":
            return PolicyDecision.DENY  # plan mode previews, never writes
        return PolicyDecision.ALLOW  # act / autonomous

    if tool_name == "shell":
        cmd = (args.get("cmd") or "").strip()
        first_token = cmd.split(" ")[0] if cmd else ""
        if first_token in SHELL_ALLOWLIST:
            return PolicyDecision.ALLOW if mode in ("act", "autonomous") else PolicyDecision.DENY
        return PolicyDecision.DENY  # non-allowlisted shell: always denied in Phase 1

    return PolicyDecision.DENY
