"""Evidence score in [0, 1] (plan §11 step 8). Phase-1 base formula — simple
and documented here, not the full Ochiai/SBFL/behavioral-diff machinery
originally scoped for later phases:

  +0.5  repro_fixed:      strictly fewer failing tests post-fix than pre-fix
                           (not "the whole suite is green" — unrelated
                           pre-existing failures elsewhere shouldn't block
                           credit for the fix actually made)
  +0.3  no_new_failures:  every post-fix failure already existed pre-fix
  +0.1  executor_completed: the model called done() rather than aborting
  +0.1  edit_compiled:    no edit was auto-reverted for a syntax error

Phase 2 layers one optional penalty on top (plan §11 step 5), without
touching the base formula above:

  -0.1  collateral_changes: raven.verify.behavior_diff found a function
                           other than the intended target whose output
                           changed too (floor 0.0) — a soft signal, not a
                           hard failure; this doesn't affect pass/fail
                           without also affecting no_new_failures.
"""

from __future__ import annotations

from raven.verify.baseline import TestSummary


def compute_evidence(
    pre: TestSummary | None,
    post: TestSummary | None,
    executor_completed: bool,
    edit_compiled: bool = True,
    collateral_changes: list[str] | None = None,
) -> dict:
    if pre is None or post is None:
        return {
            "evidence_score": 0.1 if executor_completed else 0.0,
            "repro_fixed": None,
            "no_new_failures": None,
            "pre_failed": None,
            "post_failed": None,
            "note": "no test suite evidence available",
        }

    no_new_failures = set(post.failed) <= set(pre.failed)
    repro_fixed = no_new_failures and len(post.failed) < len(pre.failed)

    score = 0.0
    if repro_fixed:
        score += 0.5
    if no_new_failures:
        score += 0.3
    if executor_completed:
        score += 0.1
    if edit_compiled:
        score += 0.1

    if collateral_changes:
        score -= 0.1

    return {
        "evidence_score": round(max(0.0, min(score, 1.0)), 2),
        "repro_fixed": repro_fixed,
        "no_new_failures": no_new_failures,
        "pre_failed": pre.failed,
        "post_failed": post.failed,
        "collateral_changes": collateral_changes or [],
    }
