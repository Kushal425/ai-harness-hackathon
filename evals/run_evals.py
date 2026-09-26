#!/usr/bin/env python3
"""Eval harness (plan §15, Step 4): runs Raven against toy_repo task
variants using a FakeClient scripted with the correct action sequence, so
the whole pipeline (tools, executor, orchestrator, verifier) is exercised
and produces a reproducible baseline number without a live API key —
mirroring how `make test` works offline. Scoring is independent of the
Judge's own verdict: each task's `check` re-runs a ground-truth test (or
inspects the summary) against the final repo state."""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from raven.config import RunSettings  # noqa: E402
from raven.core.orchestrator import run_orchestrator  # noqa: E402
from raven.llm.fake import FakeClient  # noqa: E402
from raven.llm.gateway import LLMGateway  # noqa: E402
from raven.tools.registry import RunContext, build_default_registry  # noqa: E402

TASKS_DIR = Path(__file__).parent / "tasks"
WORK_DIR = Path(__file__).parent / "work"
RESULTS_DIR = Path(__file__).parent / "results"
TOY_REPO = REPO_ROOT / "tests" / "fixtures" / "toy_repo"  # default fixture (also used by raven/learn/tune.py)


def score_task(check: dict, repo_dir: Path, orchestrator_result) -> tuple[bool, str]:
    if not orchestrator_result.accepted:
        return False, f"judge rejected: {orchestrator_result.reason}"

    if check.get("test_target"):
        registry = build_default_registry()
        ctx = RunContext(repo_root=repo_dir, mode="act")
        result = registry.dispatch("tests", {"target": check["test_target"]}, ctx)
        if not result.ok:
            return False, f"ground-truth check failed: {check['test_target']}"

    if check.get("hidden_test"):
        # A grader test the agent never saw, like the hackathon's hidden
        # tests: written in only after the run finishes, then removed.
        hidden = repo_dir / ".raven" / "hidden" / "test_hidden.py"
        hidden.parent.mkdir(parents=True, exist_ok=True)
        hidden.write_text(check["hidden_test"])
        try:
            result = build_default_registry().dispatch(
                "tests", {"target": str(hidden.relative_to(repo_dir))}, RunContext(repo_root=repo_dir, mode="act"),
            )
        finally:
            hidden.unlink(missing_ok=True)
        if not result.ok:
            return False, "hidden test failed"

    if check.get("expect_repro_verified"):
        repro = (orchestrator_result.evidence or {}).get("repro") or {}
        if not repro.get("verified"):
            return False, f"reproduction not verified fail->pass: {repro}"

    if check.get("summary_contains"):
        summary = orchestrator_result.executor_result.summary.lower()
        missing = [s for s in check["summary_contains"] if s.lower() not in summary]
        if missing:
            return False, f"summary missing expected terms: {missing}"

    if check.get("expect_no_edits") and orchestrator_result.executor_result.touched_paths:
        return False, f"expected no edits, got: {orchestrator_result.executor_result.touched_paths}"

    return True, "ok"


def run_task(task_path: Path) -> dict:
    task = yaml.safe_load(task_path.read_text())
    task_id = task["id"]

    repo_dir = WORK_DIR / task_id
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    fixture = TOY_REPO.parent / task["fixture"] if task.get("fixture") else TOY_REPO
    shutil.copytree(fixture, repo_dir, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))

    gateway = LLMGateway(FakeClient(scripted_responses=list(task["scripted_responses"])))

    start = time.time()
    # Scripted responses are consumed in order, so candidate calls run
    # sequentially here (parallel_calls only changes latency, not results).
    settings = RunSettings(candidates=task.get("candidates", 3), parallel_calls=False, trace=True)
    result = run_orchestrator(
        gateway, repo_dir, goal=task["goal"], strategy=task.get("strategy", "single_loop"), settings=settings,
    )
    elapsed = time.time() - start

    resolved, reason = score_task(task.get("check", {}), repo_dir, result)

    return {
        "id": task_id,
        "task_type": task.get("task_type", ""),
        "strategy": task.get("strategy", "single_loop"),
        "resolved": resolved,
        "reason": "" if resolved else reason,
        "tokens": gateway.stats.total_tokens,
        "tool_calls": result.executor_result.tool_calls,
        "llm_calls": gateway.stats.calls,
        "seconds": round(elapsed, 3),
    }


def render_table(rows: list[dict]) -> str:
    total = len(rows)
    resolved_count = sum(1 for r in rows if r["resolved"])
    avg_tokens = sum(r["tokens"] for r in rows) / total if total else 0
    avg_tool_calls = sum(r["tool_calls"] for r in rows) / total if total else 0

    lines = [
        "# Raven eval baseline",
        "",
        f"Resolved: {resolved_count}/{total}  ·  avg tokens: {avg_tokens:.0f}  ·  "
        f"avg tool calls: {avg_tool_calls:.1f}",
        "",
        "| id | task_type | strategy | resolved | tokens | tool_calls | llm_calls | seconds | reason |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['task_type']} | {r['strategy']} | {'yes' if r['resolved'] else 'no'} | "
            f"{r['tokens']} | {r['tool_calls']} | {r['llm_calls']} | {r['seconds']} | {r['reason']} |"
        )
    return "\n".join(lines)


def main() -> int:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    task_paths = sorted(TASKS_DIR.glob("*.yaml"))
    rows = [run_task(p) for p in task_paths]

    report = render_table(rows)
    (RESULTS_DIR / "baseline.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
