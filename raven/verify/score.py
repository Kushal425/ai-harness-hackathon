"""Evidence score in [0, 1] (plan §11 step 8). Phase-1 formula — simple and
documented here, not the full Ochiai/SBFL/behavioral-diff machinery of
later phases:

  +0.5  repro_fixed:      strictly fewer failing tests post-fix than pre-fix
                           (not "the whole suite is green" — unrelated
                           pre-existing failures elsewhere shouldn't block
                           credit for the fix actually made)
  +0.3  no_new_failures:  every post-fix failure already existed pre-fix
  +0.1  executor_completed: the model called done() rather than aborting
  +0.1  edit_compiled:    no edit was auto-reverted for a syntax error
"""

from __future__ import annotations

from raven.verify.baseline import TestSummary


def compute_evidence(
    pre: TestSummary | None,
    post: TestSummary | None,
    executor_completed: bool,
    edit_compiled: bool = True,
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

    return {
        "evidence_score": round(min(score, 1.0), 2),
        "repro_fixed": repro_fixed,
        "no_new_failures": no_new_failures,
        "pre_failed": pre.failed,
        "post_failed": post.failed,
    }
