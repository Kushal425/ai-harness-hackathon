"""Judge (plan §6.1, §11 Judge rule): decides whether the run is done based
on evidence, not the model's say-so. If evidence wasn't collected at all
(no test suite in the repo), a completed executor run is trusted weakly —
that's the honest degrade-when-we-can't-verify case."""

from __future__ import annotations

from dataclasses import dataclass

from raven.core.executor import ExecutorResult

EVIDENCE_ACCEPT_THRESHOLD = 0.5


@dataclass
class Verdict:
    accepted: bool
    reason: str


def judge(executor_result: ExecutorResult, evidence: dict | None) -> Verdict:
    if not executor_result.completed:
        return Verdict(False, executor_result.aborted_reason or "executor did not complete")

    if evidence is None:
        return Verdict(True, "executor reported done; no test suite detected to gather evidence from")

    if evidence.get("pre_failed") is None:
        return Verdict(True, "executor reported done; " + evidence.get("note", "no evidence available"))

    if evidence["evidence_score"] >= EVIDENCE_ACCEPT_THRESHOLD and evidence["no_new_failures"]:
        return Verdict(
            True,
            f"evidence score {evidence['evidence_score']} meets threshold; no new test failures",
        )

    return Verdict(False, f"insufficient evidence (score {evidence['evidence_score']}): {evidence}")
