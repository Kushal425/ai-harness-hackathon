"""Runs the test suite and extracts a compact pass/fail summary, including
failing test names, so pre-fix and post-fix runs can be diffed (plan §11
step 1: baseline)."""

from __future__ import annotations

from dataclasses import dataclass, field

from raven.tools.registry import RunContext, ToolRegistry
from raven.tools.test_runner import FAILURE_RE


@dataclass
class TestSummary:
    __test__ = False  # not a pytest test case despite the name

    passed: bool
    output: str
    failed: list[str] = field(default_factory=list)


def run_test_summary(registry: ToolRegistry, ctx: RunContext, target: str | None = None) -> TestSummary:
    args = {"target": target} if target else {}
    result = registry.dispatch("tests", args, ctx)
    data = result.data or {}
    failed = data["failed"] if "failed" in data else FAILURE_RE.findall(result.output)
    return TestSummary(passed=result.ok, output=result.output, failed=failed)
