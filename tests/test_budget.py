"""Run budgets (plan §13) and config.yaml wiring: budgets.* stop a run,
the 80% nudge reaches the model, and config values are what the
orchestrator actually uses."""

import shutil
from pathlib import Path

import pytest

from raven.config import RunSettings, load_config
from raven.core.budget import Budget
from raven.core.orchestrator import run_orchestrator
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway

TOY_REPO = Path(__file__).parent / "fixtures" / "toy_repo"
READ = '```action\n{"tool": "read", "args": {"path": "calc/arithmetic.py"}}\n```'


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(TOY_REPO, dest)
    return dest


def test_token_budget_stops_the_run(repo):
    gateway = LLMGateway(FakeClient([READ] * 50))
    result = run_orchestrator(
        gateway, repo, "fix the failing average() test", max_iterations=50, reproduce=False,
        settings=RunSettings(budget_tokens=2000, trace=False, lessons=False),
    )
    assert not result.accepted
    assert "budget exhausted" in result.reason and "token" in result.reason
    assert result.executor_result.iterations < 50


def test_budget_nudges_the_model_to_converge_at_80_percent():
    class Stats:
        total_tokens = 0

    class Gw:
        stats = Stats()

    budget = Budget(Gw(), total_tokens=1000)
    assert budget.warning() is None
    Gw.stats.total_tokens = 850
    assert "Converge now" in budget.warning()
    assert budget.warning() is None  # only once
    Gw.stats.total_tokens = 1000
    assert "token budget exhausted" in budget.exhausted()


def test_budget_nudge_reaches_the_model_context(repo):
    client = FakeClient([READ] * 50)
    run_orchestrator(
        LLMGateway(client), repo, "fix the failing average() test", max_iterations=50, reproduce=False,
        settings=RunSettings(budget_tokens=10000, trace=False, lessons=False),  # ~1k tokens/turn
    )
    seen = "\n".join(m.content for call in client.calls for m in call)
    assert "[budget]" in seen and "Converge now" in seen


def test_config_yaml_values_are_the_ones_the_run_uses():
    kwargs = load_config().run_kwargs()
    settings = kwargs["settings"]
    assert kwargs["max_iterations"] == 30 and kwargs["reproduce"] is True
    assert settings.budget_tokens == 400000 and settings.budget_seconds == 1200
    assert settings.stall_turns == 4 and settings.identical_failures_for_debugger == 3


def test_every_config_key_is_read():
    """No dead knobs: each key in config.yaml maps to something the code uses."""
    import yaml

    raw = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
    read_by_code = {
        "llm": {"provider", "base_url", "model", "providers", "temperature", "seed", "max_output_tokens",
                "tool_protocol", "request_timeout_s", "max_retries"},
        "executor": {"strategy", "candidates", "max_candidates", "parallel_calls", "max_iterations",
                     "max_replans", "delegate_min_reads"},
        "context": {"half_life"},
        "recovery": {"stall_turns", "identical_failures_for_debugger"},
        "verify": {"reproduce", "trace", "behavior_diff"},
        "learning": {"in_run_reflection", "lessons", "max_lessons_pinned"},
        "budgets": {"total_tokens", "wall_clock_s"},
        "ui": {"tui"},
    }
    assert set(raw) == set(read_by_code)
    for section, keys in raw.items():
        assert set(keys) <= read_by_code[section], (section, set(keys) - read_by_code[section])


def test_trace_off_in_config_skips_tracing(repo):
    done_fix = [
        '```action\n{"tool": "edit", "args": {"path": "calc/arithmetic.py", "search": '
        '"return sum(numbers) / (len(numbers) + 1)", "replace": "return sum(numbers) / len(numbers)"}}\n```',
        '```action\n{"tool": "done", "args": {"summary": "fixed"}}\n```',
    ]
    result = run_orchestrator(
        LLMGateway(FakeClient(done_fix)), repo, "fix the failing average() test", reproduce=False,
        settings=RunSettings(trace=False, lessons=False),
    )
    assert result.accepted
    assert "Execution story" not in result.report_path.read_text()
