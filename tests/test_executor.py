import shutil
from pathlib import Path

import pytest

from raven.core.executor import run_single_loop
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway
from raven.recovery.checkpoints import CheckpointManager
from raven.tools.registry import RunContext, build_default_registry

TOY_REPO = Path(__file__).parent / "fixtures" / "toy_repo"

FIX_SEQUENCE = [
    '```action\n{"tool": "search", "args": {"pattern": "def average"}}\n```',
    '```action\n{"tool": "read", "args": {"path": "calc/arithmetic.py"}}\n```',
    '```action\n{"tool": "edit", "args": {"path": "calc/arithmetic.py", '
    '"search": "return sum(numbers) / (len(numbers) + 1)", '
    '"replace": "return sum(numbers) / len(numbers)"}}\n```',
    '```action\n{"tool": "tests", "args": {"target": "tests/test_arithmetic.py"}}\n```',
    '```action\n{"tool": "done", "args": {"summary": "fixed off-by-one bug in average()"}}\n```',
]


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(TOY_REPO, dest)
    return dest


def test_single_loop_fixes_toy_bug_and_leaves_clean_diff(repo):
    gateway = LLMGateway(FakeClient(scripted_responses=list(FIX_SEQUENCE)))
    registry = build_default_registry()
    ctx = RunContext(repo_root=repo, mode="act")
    checkpoints = CheckpointManager(repo)

    before = registry.dispatch("tests", {"target": "tests/test_arithmetic.py"}, ctx)
    assert not before.ok  # bug present before the fix

    result = run_single_loop(gateway, registry, ctx, checkpoints, goal="fix the failing average() test")

    assert result.completed
    assert "fixed" in result.summary.lower()
    assert result.touched_paths == ["calc/arithmetic.py"]

    after = registry.dispatch("tests", {"target": "tests/test_arithmetic.py"}, ctx)
    assert after.ok  # bug fixed after the run

    # no stray files, no leftover checkpoint state
    assert not (repo / ".raven" / "runs").exists()


def test_single_loop_restores_clean_tree_on_malformed_output_abort(repo):
    original = (repo / "calc/arithmetic.py").read_text()
    scripted = [
        '```action\n{"tool": "edit", "args": {"path": "calc/arithmetic.py", '
        '"search": "return sum(numbers) / (len(numbers) + 1)", '
        '"replace": "return sum(numbers) / len(numbers)"}}\n```',
        "no action block here, oops",
        "still no action block",
        "and again, nothing parseable",
    ]
    gateway = LLMGateway(FakeClient(scripted_responses=scripted))
    registry = build_default_registry()
    ctx = RunContext(repo_root=repo, mode="act")
    checkpoints = CheckpointManager(repo)

    result = run_single_loop(gateway, registry, ctx, checkpoints, goal="fix the failing average() test")

    assert not result.completed
    assert result.aborted_reason and "malformed" in result.aborted_reason
    assert (repo / "calc/arithmetic.py").read_text() == original  # restored, not left half-applied


def test_single_loop_stops_at_budget_and_keeps_partial_progress(repo):
    # Budget exhaustion is not a failure to recover from — it hands partial
    # progress to the (Step 5/6) Judge/Verifier rather than reverting, unlike
    # the malformed-output-abort and unhandled-exception cases above.
    edit_action = (
        '```action\n{"tool": "edit", "args": {"path": "calc/arithmetic.py", '
        '"search": "return sum(numbers) / (len(numbers) + 1)", '
        '"replace": "return sum(numbers) / len(numbers)"}}\n```'
    )
    read_action = '```action\n{"tool": "read", "args": {"path": "calc/arithmetic.py"}}\n```'
    scripted = [edit_action] + [read_action] * 5  # never calls done
    gateway = LLMGateway(FakeClient(scripted_responses=scripted))
    registry = build_default_registry()
    ctx = RunContext(repo_root=repo, mode="act")
    checkpoints = CheckpointManager(repo)

    result = run_single_loop(
        gateway, registry, ctx, checkpoints, goal="fix the bug", max_iterations=3
    )

    assert not result.completed
    assert "budget exhausted" in result.aborted_reason
    assert result.touched_paths == ["calc/arithmetic.py"]
    assert "return sum(numbers) / len(numbers)" in (repo / "calc/arithmetic.py").read_text()
