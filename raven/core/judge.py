"""Judge (plan §6.1, §11 Judge rule): decides whether the run is done based
on evidence, not the model's say-so. If evidence wasn't collected at all
(no test suite in the repo), a completed executor run is trusted weakly —
that's the honest degrade-when-we-can't-verify case."""

from __future__ import annotations

import re
from dataclasses import dataclass

from raven.core.executor import ExecutorResult

EVIDENCE_ACCEPT_THRESHOLD = 0.5

# Task types whose correct outcome is an answer, not a patch.
NO_CHANGE_TASK_TYPES = {"question", "review"}
# Fallback when there's no Understanding (single_loop never computes one).
# Deliberately conservative: anything that doesn't clearly read as a
# question is treated as a change request.
_QUESTION_RE = re.compile(
    r"^\s*(why|what|how|where|which|explain|describe)\b|\?\s*$|without chang",
    re.IGNORECASE,
)


@dataclass
class Verdict:
    accepted: bool
    reason: str
    # False when accepted without any test evidence (no pytest suite): the
    # patch is kept, but the outcome is labelled unverified, never plain RESOLVED
    verified: bool = True


def outcome_label(accepted: bool, verified: bool = True) -> str:
    if not accepted:
        return "UNRESOLVED"
    return "RESOLVED" if verified else "RESOLVED (unverified)"


def expects_changes(goal: str, understanding=None) -> bool:
    """Whether a correct run of this task must leave a patch behind."""
    if understanding is not None:
        return understanding.task_type not in NO_CHANGE_TASK_TYPES
    return not _QUESTION_RE.search(goal)


def judge(executor_result: ExecutorResult, evidence: dict | None, expects_changes: bool = False) -> Verdict:
    if not executor_result.completed:
        return Verdict(False, executor_result.aborted_reason or "executor did not complete")

    # Evidence over claims: done() with an empty diff on a fix/feature task
    # is a claim with nothing behind it, whatever the test score says (an
    # unchanged tree trivially has "no new failures").
    if expects_changes and not executor_result.touched_paths:
        return Verdict(False, "model reported done but changed no files")

    # The model's own reproduction test still failing on its fix is direct
    # evidence the fix doesn't work, whatever the rest of the suite says.
    repro = (evidence or {}).get("repro")
    if repro and not repro.get("passes_after"):
        return Verdict(False, "reproduction test still fails after the fix")

    if evidence is None:
        return Verdict(True, "UNVERIFIED: model reported done; no test suite to gather evidence from",
                       verified=False)

    if evidence.get("pre_failed") is None:
        return Verdict(True, "UNVERIFIED: model reported done; " + evidence.get("note", "no evidence available"),
                       verified=False)

    if evidence["evidence_score"] >= EVIDENCE_ACCEPT_THRESHOLD and evidence["no_new_failures"]:
        return Verdict(
            True,
            f"evidence score {evidence['evidence_score']} meets threshold; no new test failures",
        )

    return Verdict(False, f"insufficient evidence (score {evidence['evidence_score']}): {evidence}")
