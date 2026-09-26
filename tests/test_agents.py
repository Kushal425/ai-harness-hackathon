import shutil
import subprocess
from pathlib import Path

import pytest

from raven.agents.explorer import explore, should_delegate_to_explorer
from raven.agents.reviewer import review_diff
from raven.core.orchestrator import run_orchestrator
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway

TOY_REPO = Path(__file__).parent / "fixtures" / "toy_repo"


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(TOY_REPO, dest)
    return dest


@pytest.fixture
def git_repo(repo):
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


# -- should_delegate_to_explorer ---------------------------------------------


def test_should_delegate_on_exploratory_keyword():
    assert should_delegate_to_explorer("figure out how does the parser work") is True


def test_should_delegate_on_long_vague_step():
    long_step = "make some changes across the codebase to improve consistency and readability overall"
    assert should_delegate_to_explorer(long_step, delegate_min_reads=4) is True


def test_should_not_delegate_short_concrete_step():
    assert should_delegate_to_explorer("fix the off-by-one in average()") is False


# -- Explorer ----------------------------------------------------------------


def test_explorer_finds_a_symbol(repo):
    responses = [
        '```action\n{"tool": "search", "args": {"pattern": "def average"}}\n```',
        '```action\n{"tool": "done", "args": {"summary": "average() is defined in calc/arithmetic.py:9"}}\n```',
    ]
    gateway = LLMGateway(FakeClient(scripted_responses=responses))
    finding = explore(gateway, repo, "where is average() defined?")
    assert "arithmetic.py" in finding


def test_explorer_degrades_gracefully_when_it_cant_conclude(repo):
    gateway = LLMGateway(FakeClient(scripted_responses=["not an action block"] * 10))
    finding = explore(gateway, repo, "where is average() defined?", max_iterations=2)
    assert "could not reach a conclusion" in finding or "Explorer" in finding


# -- Reviewer ------------------------------------------------------------------


def test_review_diff_reports_no_diff_when_clean(git_repo):
    gateway = LLMGateway(FakeClient())
    result = review_diff(gateway, git_repo, evidence=None)
    assert result == "no diff to review"


def test_review_diff_skips_trivial_diffs_without_a_model_call(git_repo):
    (git_repo / "calc" / "arithmetic.py").write_text(
        (git_repo / "calc" / "arithmetic.py").read_text() + "\n# tiny comment\n"
    )
    gateway = LLMGateway(FakeClient(scripted_responses=["should not be consumed"]))
    result = review_diff(gateway, git_repo, evidence={"evidence_score": 1.0})
    assert "trivial" in result
    assert gateway.stats.calls == 0  # confirms no model call was made


def test_review_diff_calls_model_for_a_substantial_diff(git_repo):
    big_change = "\n".join(f"def extra_{i}():\n    return {i}\n" for i in range(10))
    (git_repo / "calc" / "arithmetic.py").write_text(
        (git_repo / "calc" / "arithmetic.py").read_text() + "\n" + big_change
    )
    review_response = '```review\n{"verdict": "approve", "issues": []}\n```'
    gateway = LLMGateway(FakeClient(scripted_responses=[review_response]))
    result = review_diff(gateway, git_repo, evidence={"evidence_score": 1.0})
    assert "approve" in result
    assert gateway.stats.calls == 1


def test_review_diff_reports_issues_with_severity(git_repo):
    big_change = "\n".join(f"def extra_{i}():\n    return {i}\n" for i in range(10))
    (git_repo / "calc" / "arithmetic.py").write_text(
        (git_repo / "calc" / "arithmetic.py").read_text() + "\n" + big_change
    )
    review_response = (
        '```review\n{"verdict": "concerns", "issues": '
        '[{"description": "off-by-one still possible", "severity": "high"}]}\n```'
    )
    gateway = LLMGateway(FakeClient(scripted_responses=[review_response]))
    result = review_diff(gateway, git_repo, evidence={"evidence_score": 0.5})
    assert "high" in result
    assert "off-by-one" in result


# -- delegated strategy end-to-end -------------------------------------------


UNDERSTAND_RESP = (
    '```understanding\n{"task_type": "bug_fix", "summary": "fix average() off-by-one", '
    '"entities": {"files": ["calc/arithmetic.py"], "symbols": ["average"], "errors": [], "commands": []}, '
    '"acceptance_criteria": ["test_average passes"], "verification_strategy": "existing_tests", '
    '"ambiguities": [], "risk": "low"}\n```'
)
PLAN_RESP = (
    '```plan\n{"steps": [{"action": "fix the off-by-one in average()", "tools": ["edit"], '
    '"check": "test_average passes"}]}\n```'
)
FIX_SEQUENCE = [
    '```action\n{"tool": "edit", "args": {"path": "calc/arithmetic.py", '
    '"search": "return sum(numbers) / (len(numbers) + 1)", '
    '"replace": "return sum(numbers) / len(numbers)"}}\n```',
    '```action\n{"tool": "done", "args": {"summary": "fixed off-by-one bug in average()"}}\n```',
]


def test_delegated_strategy_resolves_without_triggering_explorer_on_a_concrete_step(repo):
    # "fix the off-by-one in average()" doesn't match exploratory keywords
    # and is short, so should_delegate_to_explorer is False — no extra
    # Explorer model call should be consumed from the script.
    gateway = LLMGateway(FakeClient(scripted_responses=[UNDERSTAND_RESP, PLAN_RESP] + FIX_SEQUENCE))
    result = run_orchestrator(gateway, repo, goal="fix average()", strategy="delegated")
    assert result.accepted
    assert result.executor_result.touched_paths == ["calc/arithmetic.py"]


def test_delegated_strategy_invokes_explorer_for_an_exploratory_step(repo):
    plan_with_exploration = (
        '```plan\n{"steps": [{"action": "investigate how does average() compute its result", '
        '"tools": ["read"], "check": "understood"}, '
        '{"action": "fix the off-by-one in average()", "tools": ["edit"], "check": "test_average passes"}]}\n```'
    )
    explorer_sequence = [
        '```action\n{"tool": "read", "args": {"path": "calc/arithmetic.py"}}\n```',
        '```action\n{"tool": "done", "args": {"summary": "average() divides by len(numbers)+1, off by one"}}\n```',
    ]
    step1_sequence = ['```action\n{"tool": "done", "args": {"summary": "understood, moving to next step"}}\n```']

    scripted = [UNDERSTAND_RESP, plan_with_exploration] + explorer_sequence + step1_sequence + FIX_SEQUENCE
    gateway = LLMGateway(FakeClient(scripted_responses=scripted))
    result = run_orchestrator(gateway, repo, goal="fix average()", strategy="delegated")
    assert result.accepted
