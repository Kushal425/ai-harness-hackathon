from raven.core.executor import ExecutorResult
from raven.core.judge import judge
from raven.verify.baseline import TestSummary
from raven.verify.score import compute_evidence


def test_compute_evidence_full_marks_for_clean_fix():
    pre = TestSummary(passed=False, output="", failed=["tests/x.py::test_a"])
    post = TestSummary(passed=True, output="", failed=[])
    evidence = compute_evidence(pre, post, executor_completed=True)
    assert evidence["evidence_score"] == 1.0
    assert evidence["repro_fixed"] is True
    assert evidence["no_new_failures"] is True


def test_compute_evidence_credits_partial_fix_among_unrelated_failures():
    # pre-existing, unrelated failure in another module shouldn't block
    # credit for fixing the one the task targeted.
    pre = TestSummary(passed=False, output="", failed=["tests/x.py::test_a", "tests/y.py::test_unrelated"])
    post = TestSummary(passed=False, output="", failed=["tests/y.py::test_unrelated"])
    evidence = compute_evidence(pre, post, executor_completed=True)
    assert evidence["repro_fixed"] is True
    assert evidence["no_new_failures"] is True
    assert evidence["evidence_score"] == 1.0


def test_compute_evidence_penalizes_new_failures():
    pre = TestSummary(passed=False, output="", failed=["tests/x.py::test_a"])
    post = TestSummary(passed=False, output="", failed=["tests/x.py::test_a", "tests/y.py::test_b"])
    evidence = compute_evidence(pre, post, executor_completed=True)
    assert evidence["no_new_failures"] is False
    assert evidence["evidence_score"] < 0.5


def test_compute_evidence_none_when_no_test_suite():
    evidence = compute_evidence(None, None, executor_completed=True)
    assert evidence["pre_failed"] is None


def test_judge_rejects_incomplete_executor():
    result = ExecutorResult(completed=False, summary="", iterations=1, tool_calls=0, aborted_reason="oops")
    verdict = judge(result, evidence=None)
    assert not verdict.accepted
    assert "oops" in verdict.reason


def test_judge_accepts_with_no_evidence_available():
    result = ExecutorResult(completed=True, summary="done", iterations=1, tool_calls=1)
    verdict = judge(result, evidence=None)
    assert verdict.accepted


def test_judge_accepts_strong_evidence():
    result = ExecutorResult(completed=True, summary="done", iterations=1, tool_calls=1)
    evidence = {"evidence_score": 1.0, "no_new_failures": True, "pre_failed": ["x"], "post_failed": []}
    verdict = judge(result, evidence)
    assert verdict.accepted


def test_judge_rejects_weak_evidence():
    result = ExecutorResult(completed=True, summary="done", iterations=1, tool_calls=1)
    evidence = {"evidence_score": 0.1, "no_new_failures": False, "pre_failed": ["x"], "post_failed": ["x", "y"]}
    verdict = judge(result, evidence)
    assert not verdict.accepted
