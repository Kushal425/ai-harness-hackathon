import shutil
from pathlib import Path

import pytest

from raven.core.orchestrator import run_orchestrator
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway

TOY_REPO = Path(__file__).parent / "fixtures" / "toy_repo"

FIX_ACTIONS = [
    '```action\n{"tool": "search", "args": {"pattern": "def average"}}\n```',
    '```action\n{"tool": "read", "args": {"path": "calc/arithmetic.py"}}\n```',
    '```action\n{"tool": "edit", "args": {"path": "calc/arithmetic.py", '
    '"search": "return sum(numbers) / (len(numbers) + 1)", '
    '"replace": "return sum(numbers) / len(numbers)"}}\n```',
    '```action\n{"tool": "done", "args": {"summary": "fixed off-by-one bug in average()"}}\n```',
]

UNDERSTANDING_RESPONSE = """\
```understanding
{"task_type": "bug_fix", "summary": "fix average()", "entities": {"files": ["calc/arithmetic.py"]},
 "acceptance_criteria": ["test_average passes"], "verification_strategy": "existing_tests",
 "ambiguities": [], "risk": "low"}
```
"""

PLAN_RESPONSE = """\
```plan
{"steps": [{"action": "fix the off-by-one in average()", "tools": ["edit"], "check": "test_average passes"}]}
```
"""


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(TOY_REPO, dest)
    return dest


def test_single_loop_orchestrator_resolves_and_writes_report(repo):
    gateway = LLMGateway(FakeClient(scripted_responses=list(FIX_ACTIONS)))
    result = run_orchestrator(gateway, repo, goal="fix the failing average() test", strategy="single_loop")

    assert result.accepted
    assert result.evidence["repro_fixed"] is True
    assert result.report_path.exists()
    report_text = result.report_path.read_text()
    assert "RESOLVED" in report_text


def test_plan_execute_orchestrator_resolves_and_writes_report(repo):
    scripted = [UNDERSTANDING_RESPONSE, PLAN_RESPONSE] + FIX_ACTIONS
    gateway = LLMGateway(FakeClient(scripted_responses=scripted))
    result = run_orchestrator(gateway, repo, goal="fix the failing average() test", strategy="plan_execute")

    assert result.accepted
    assert result.understanding.task_type == "bug_fix"
    assert result.plan is not None and len(result.plan.steps) == 1
    assert result.evidence["repro_fixed"] is True


def test_single_loop_orchestrator_reports_failure_honestly(repo):
    # Never calls done — executor aborts on budget exhaustion, Judge rejects.
    read_action = '```action\n{"tool": "read", "args": {"path": "calc/arithmetic.py"}}\n```'
    gateway = LLMGateway(FakeClient(scripted_responses=[read_action] * 20))
    result = run_orchestrator(
        gateway, repo, goal="fix the failing average() test", strategy="single_loop", max_iterations=3
    )

    assert not result.accepted
    assert "budget exhausted" in result.reason
    report_text = result.report_path.read_text()
    assert "UNRESOLVED" in report_text
