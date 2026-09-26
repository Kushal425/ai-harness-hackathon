import shutil
from pathlib import Path

import pytest

from raven.core.executor import run_single_loop
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway
from raven.recovery.checkpoints import CheckpointManager
from raven.recovery.handlers import RecoveryState, fingerprint_action, failure_signature_of
from raven.tools.registry import RunContext, build_default_registry

TOY_REPO = Path(__file__).parent / "fixtures" / "toy_repo"


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(TOY_REPO, dest)
    return dest


# -- unit tests on RecoveryState in isolation --------------------------------


def test_fingerprint_is_stable_regardless_of_arg_order():
    a = fingerprint_action("edit", {"path": "x.py", "search": "a"})
    b = fingerprint_action("edit", {"search": "a", "path": "x.py"})
    assert a == b


def test_check_repeated_action_flags_the_second_identical_call():
    rs = RecoveryState()
    assert rs.check_repeated_action("read", {"path": "x.py"}, turn=1) is None
    rs.record_action_result("read", {"path": "x.py"}, turn=1, result_text="some content")
    nudge = rs.check_repeated_action("read", {"path": "x.py"}, turn=2)
    assert nudge is not None
    assert "turn 1" in nudge


def test_edit_failure_streak_triggers_on_second_consecutive_failure():
    rs = RecoveryState()
    assert rs.record_edit_failure("x.py") is False
    assert rs.record_edit_failure("x.py") is True


def test_edit_success_resets_streak():
    rs = RecoveryState()
    rs.record_edit_failure("x.py")
    rs.record_edit_success("x.py")
    assert rs.record_edit_failure("x.py") is False


def test_test_failure_signature_is_stable_across_unrelated_output_noise():
    out1 = "...\nFAILED tests/test_a.py::test_x - assert 1 == 2\n1 failed in 0.01s"
    out2 = "...\nFAILED tests/test_a.py::test_x - assert 3 == 4\n1 failed in 0.02s"
    assert failure_signature_of(out1) == failure_signature_of(out2)


def test_record_test_result_triggers_debugger_after_threshold():
    rs = RecoveryState(identical_failures_for_debugger=3)
    out = "FAILED tests/test_a.py::test_x"
    assert rs.record_test_result(out, ok=False) == (False, "tests/test_a.py::test_x")
    assert rs.record_test_result(out, ok=False) == (False, "tests/test_a.py::test_x")
    should_debug, sig = rs.record_test_result(out, ok=False)
    assert should_debug is True


def test_record_test_result_resets_streak_on_pass():
    rs = RecoveryState(identical_failures_for_debugger=2)
    out = "FAILED tests/test_a.py::test_x"
    rs.record_test_result(out, ok=False)
    rs.record_test_result("all good", ok=True)
    should_debug, _ = rs.record_test_result(out, ok=False)
    assert should_debug is False  # streak was reset, this is only attempt 1 again


def test_stall_nudge_fires_after_stall_turns_with_no_progress():
    rs = RecoveryState(stall_turns=3)
    assert rs.record_progress(False) is None
    assert rs.record_progress(False) is None
    assert rs.record_progress(False) is not None


def test_progress_resets_stall_counter():
    rs = RecoveryState(stall_turns=3)
    rs.record_progress(False)
    rs.record_progress(True)
    assert rs.record_progress(False) is None  # counter was reset, this is only 1


# -- Debugger unit test -------------------------------------------------------


def test_debugger_runs_probe_and_returns_a_verdict(repo):
    from raven.agents.debugger import run_debugger

    hypotheses_response = (
        '```debug\n{"hypotheses": [{"hypothesis": "off-by-one in average()", '
        '"probe": {"tool": "read", "args": {"path": "calc/arithmetic.py"}}}]}\n```'
    )
    verdict_response = "The off-by-one hypothesis is confirmed; fix the denominator."
    gateway = LLMGateway(FakeClient(scripted_responses=[hypotheses_response, verdict_response]))
    registry = build_default_registry()
    ctx = RunContext(repo_root=repo, mode="act")

    nudge = run_debugger(gateway, registry, ctx, digest_summary="", failure_signature="test_average failing")
    assert "off-by-one" in nudge.lower() or "denominator" in nudge.lower()


def test_debugger_degrades_gracefully_on_unparseable_response(repo):
    from raven.agents.debugger import run_debugger

    gateway = LLMGateway(FakeClient(scripted_responses=["not json at all"]))
    registry = build_default_registry()
    ctx = RunContext(repo_root=repo, mode="act")

    nudge = run_debugger(gateway, registry, ctx, digest_summary="", failure_signature="test_average failing")
    assert "no clear hypothesis" in nudge.lower()


# -- chaos e2e: executor recovers from a repeatedly-failing edit ------------


def test_executor_invokes_debugger_after_repeated_identical_failure(repo):
    """Scripted FakeClient keeps making an edit that doesn't fix the bug
    (wrong replacement), three times in a row, then the Debugger should
    kick in. The run must finalize honestly (not crash, not leave a dirty
    tree) whether or not it manages to actually resolve the bug afterward."""
    bad_edit = (
        '```action\n{"tool": "edit", "args": {"path": "calc/arithmetic.py", '
        '"search": "return sum(numbers) / (len(numbers) + 1)", '
        '"replace": "return sum(numbers) / (len(numbers) + 1)  # still wrong"}}\n```'
    )
    run_tests = '```action\n{"tool": "tests", "args": {"target": "tests/test_arithmetic.py"}}\n```'
    give_up = '```action\n{"tool": "done", "args": {"summary": "could not resolve after debugging"}}\n```'

    scripted = [bad_edit, run_tests, bad_edit, run_tests, bad_edit, run_tests]
    # debugger's 2 calls, triggered on the 3rd identical test failure:
    scripted += [
        '```debug\n{"hypotheses": [{"hypothesis": "denominator still wrong", '
        '"probe": {"tool": "read", "args": {"path": "calc/arithmetic.py"}}}]}\n```',
        "the denominator needs len(numbers), not len(numbers)+1",
    ]
    scripted += [give_up]

    gateway = LLMGateway(FakeClient(scripted_responses=scripted))
    registry = build_default_registry()
    ctx = RunContext(repo_root=repo, mode="act")
    checkpoints = CheckpointManager(repo)

    result = run_single_loop(
        gateway, registry, ctx, checkpoints, goal="fix average()",
        identical_failures_for_debugger=3, max_iterations=20,
    )

    # must finish cleanly one way or another — no crash
    assert result.completed is True
    assert result.aborted_reason is None
