"""Reproduce step (plan §11.2): the model writes .raven/repro/test_repro.py,
and the harness itself checks it fails on the original code and passes on
the fix. The scenario that matters is a repo whose own suite has NO failing
test for the issue (hidden grader tests) — before this step, such a fix
could never earn repro_fixed."""

import json
import subprocess

import pytest

from raven.core.orchestrator import run_orchestrator
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway

BUGGY = "def clamp(x, lo, hi):\n    return max(lo, min(x, lo))\n"  # bug: min(x, lo) should be min(x, hi)
FIXED_LINE = "    return max(lo, min(x, hi))"
SUITE = "from mathx import clamp\n\n\ndef test_clamp_low():\n    assert clamp(-5, 0, 10) == 0\n"
REPRO = "from mathx import clamp\n\n\ndef test_clamp_in_range():\n    assert clamp(5, 0, 10) == 5\n"


def _act(tool, **args):
    return "```action\n" + json.dumps({"tool": tool, "args": args}) + "\n```"


CREATE_REPRO = _act("create", path=".raven/repro/test_repro.py", content=REPRO)
RUN_REPRO = _act("tests", target=".raven/repro/test_repro.py")
FIX = _act("edit", path="mathx.py", search="    return max(lo, min(x, lo))", replace=FIXED_LINE)
WRONG_FIX = _act("edit", path="mathx.py", search="    return max(lo, min(x, lo))", replace="    return max(lo, min(x, lo + 1))")  # clamp(5,0,10) -> 1
DONE = _act("done", summary="clamp used lo as the upper bound; fixed")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "mathx.py").write_text(BUGGY)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mathx.py").write_text(SUITE)  # passes even with the bug
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    return tmp_path


def _run(repo, script):
    return run_orchestrator(LLMGateway(FakeClient(script)), repo, goal="clamp(5, 0, 10) returns 0 instead of 5")


def test_hidden_test_bug_is_proven_fixed_by_the_reproduction(repo):
    result = _run(repo, [CREATE_REPRO, RUN_REPRO, FIX, DONE])

    assert result.accepted
    ev = result.evidence
    assert ev["pre_failed"] == [] and ev["post_failed"] == []  # the suite alone proves nothing here
    assert ev["repro"]["failed_before"] is True
    assert ev["repro"]["passes_after"] is True
    assert ev["repro_fixed"] is True and ev["evidence_score"] == 1.0

    # only the patch is left behind; the repro is archived with the report
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True)
    assert status.stdout.splitlines() == [" M mathx.py"]
    assert (repo / "mathx.py").read_text().splitlines()[1] == FIXED_LINE
    assert not (repo / ".raven" / "repro" / "test_repro.py").exists()
    assert (result.report_path.parent / "test_repro.py").exists()


def test_fix_that_doesnt_pass_its_own_reproduction_is_rejected(repo):
    result = _run(repo, [CREATE_REPRO, WRONG_FIX, DONE])

    assert not result.accepted
    assert "reproduction test still fails" in result.reason


def test_reproduction_that_never_failed_earns_no_repro_credit(repo):
    passes_on_buggy_code = "from mathx import clamp\n\n\ndef test_low():\n    assert clamp(-5, 0, 10) == 0\n"
    create = _act("create", path=".raven/repro/test_repro.py", content=passes_on_buggy_code)
    result = _run(repo, [create, FIX, DONE])

    assert result.evidence["repro"]["failed_before"] is False
    assert result.evidence["repro_fixed"] is False


def test_stale_reproduction_from_a_previous_run_is_ignored(repo):
    stale = repo / ".raven" / "repro" / "test_repro.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("def test_stale():\n    assert False\n")
    result = _run(repo, [FIX, DONE])  # this run writes no reproduction

    assert result.accepted
    assert result.evidence["repro"] is None


@pytest.mark.parametrize("goal,expected", [
    ("clamp(5, 0, 10) returns 0 instead of 5", True),
    ("Add a multiply() function to calc/arithmetic.py", True),
    ("Refactor add() for readability", False),
    ("Add docstrings to every function", False),
    ("Add an edge-case test for reverse()", False),
])
def test_repro_only_requested_for_behaviour_changes(goal, expected):
    from raven.repo.digest import RepoDigest
    from raven.verify.reproduce import repro_applicable

    assert repro_applicable(RepoDigest(test_command="pytest"), None, True, goal) is expected
    assert repro_applicable(RepoDigest(test_command=None), None, True, goal) is False  # no pytest
    assert repro_applicable(RepoDigest(test_command="pytest"), None, False, goal) is False  # a question
