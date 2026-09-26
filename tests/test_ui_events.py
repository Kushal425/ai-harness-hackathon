"""Tests for the additive on_event hook (raven/core/executor.py,
orchestrator.py, raven/session/manager.py): omitting it must be a no-op vs.
existing behavior, a raising callback must never affect the run's outcome,
and events must fire in the expected order for a simple scripted run."""

import shutil
from pathlib import Path

import pytest

from raven.core.executor import run_single_loop
from raven.core.orchestrator import run_orchestrator
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


def _run(repo, on_event=None):
    gateway = LLMGateway(FakeClient(scripted_responses=list(FIX_SEQUENCE)))
    registry = build_default_registry()
    ctx = RunContext(repo_root=repo, mode="act")
    checkpoints = CheckpointManager(repo)
    return run_single_loop(
        gateway, registry, ctx, checkpoints, goal="fix average()",
        max_iterations=10, on_event=on_event,
    )


def test_omitting_on_event_is_unchanged_from_before(repo):
    result = _run(repo, on_event=None)
    assert result.completed is True
    assert result.tool_calls == 4


def test_on_event_that_always_raises_does_not_affect_outcome(repo):
    def broken(event, data):
        raise RuntimeError("renderer exploded")

    result = _run(repo, on_event=broken)
    assert result.completed is True
    assert result.tool_calls == 4


def test_tool_start_and_tool_end_fire_in_order_per_tool_call(repo):
    events = []
    _run(repo, on_event=lambda event, data: events.append((event, data.get("tool"))))

    names = [e for e, _ in events]
    assert names == ["tool_start", "tool_end", "tool_start", "tool_end", "tool_start", "tool_end", "tool_start", "tool_end"]
    assert [t for _, t in events] == ["search", "search", "read", "read", "edit", "edit", "tests", "tests"]


def test_tool_end_carries_ok_status(repo):
    events = []
    _run(repo, on_event=lambda event, data: events.append((event, data)))

    edit_end = [d for e, d in events if e == "tool_end" and d["tool"] == "edit"][0]
    assert edit_end["ok"] is True


def test_orchestrator_verify_events_fire_and_carry_evidence(repo):
    events = []
    gateway = LLMGateway(FakeClient(scripted_responses=list(FIX_SEQUENCE)))
    result = run_orchestrator(
        gateway, repo, "fix average()", strategy="single_loop",
        on_event=lambda event, data: events.append((event, data)),
    )
    names = [e for e, _ in events]
    assert "verify_start" in names
    assert "verify_done" in names
    verify_done_data = [d for e, d in events if e == "verify_done"][0]
    assert "evidence" in verify_done_data
    assert result.accepted is True


def test_orchestrator_with_broken_on_event_still_completes_and_reports_honestly(repo):
    gateway = LLMGateway(FakeClient(scripted_responses=list(FIX_SEQUENCE)))
    result = run_orchestrator(
        gateway, repo, "fix average()", strategy="single_loop",
        on_event=lambda event, data: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert result.accepted is True
    assert result.evidence["evidence_score"] > 0
