"""Recovery handlers (plan §10.1) beyond what already lives inline in
raven/core/executor.py (LLM-error restore, malformed-output abort after 3,
unexpected-exception restore). This module adds: repeated-action detection,
edit-failure-streak escalation, stall detection, and stuck-hypothesis
detection that triggers the Debugger sub-agent. All state lives in
RecoveryState, one instance per executor run — kept out of executor.py's
loop body so that file stays readable."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from raven.tools.test_runner import FAILURE_RE


def fingerprint_action(tool: str, args: dict) -> str:
    return f"{tool}:{json.dumps(args, sort_keys=True)}"


def failure_signature_of(output: str) -> str:
    """A stable signature for a test-run result, so repeated identical
    failures can be detected across edit attempts."""
    failed = sorted(FAILURE_RE.findall(output))
    return ",".join(failed) if failed else output.strip()[:200]


@dataclass
class RecoveryState:
    stall_turns: int = 4
    identical_failures_for_debugger: int = 3

    _seen_actions: dict = field(default_factory=dict)  # fingerprint -> (turn, result_text)
    _edit_failure_streak: dict = field(default_factory=dict)  # path -> count
    _test_failure_streak: dict = field(default_factory=dict)  # signature -> count
    _turns_since_progress: int = 0

    def check_repeated_action(self, tool: str, args: dict, turn: int) -> str | None:
        """Returns a nudge if this exact action was already tried, else None
        and records it as seen (the caller should skip dispatching a repeat)."""
        fp = fingerprint_action(tool, args)
        if fp in self._seen_actions:
            prior_turn, prior_result = self._seen_actions[fp]
            return (
                f"you already ran {tool}({args}) at turn {prior_turn}; the result was: "
                f"{prior_result[:200]}. Try something different."
            )
        return None

    def record_action_result(self, tool: str, args: dict, turn: int, result_text: str) -> None:
        fp = fingerprint_action(tool, args)
        self._seen_actions[fp] = (turn, result_text)

    def record_edit_failure(self, path: str) -> bool:
        """Returns True once 2 consecutive failures on the same path have
        happened — the caller should force a fresh `read` of that region."""
        self._edit_failure_streak[path] = self._edit_failure_streak.get(path, 0) + 1
        return self._edit_failure_streak[path] >= 2

    def record_edit_success(self, path: str) -> None:
        self._edit_failure_streak.pop(path, None)

    def record_test_result(self, output: str, ok: bool) -> tuple[bool, str]:
        """Returns (debugger_should_run, signature)."""
        signature = failure_signature_of(output)
        if ok:
            self._test_failure_streak.clear()
            return False, signature
        self._test_failure_streak[signature] = self._test_failure_streak.get(signature, 0) + 1
        return self._test_failure_streak[signature] >= self.identical_failures_for_debugger, signature

    def record_progress(self, made_progress: bool) -> str | None:
        """made_progress: a write succeeded or tests were run this turn.
        Returns a stall nudge once stall_turns pass with no progress."""
        if made_progress:
            self._turns_since_progress = 0
            return None
        self._turns_since_progress += 1
        if self._turns_since_progress >= self.stall_turns:
            self._turns_since_progress = 0  # nudge once per stall window, not every turn after
            return (
                f"no progress in {self.stall_turns} turns (no successful edit, no test run). "
                "Reconsider your approach — re-read the relevant code or try a different location."
            )
        return None
