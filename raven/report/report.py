"""Writes `.raven/runs/<run_id>/report.md` and prints the
===== RAVEN RESULT ===== block used by the autonomous/piped path
(plan §5.2, §14.4)."""

from __future__ import annotations

import json
from pathlib import Path

from raven.core.judge import Verdict
from raven.llm.gateway import GatewayStats


def write_report(
    run_dir: Path,
    *,
    run_id: str,
    goal: str,
    understanding,
    plan,
    executor_result,
    evidence: dict | None,
    verdict: Verdict,
    gateway_stats: GatewayStats,
    trace_text: str | None = None,
    sbfl_results: list | None = None,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "report.md"

    lines = [
        f"# Raven run {run_id}",
        "",
        f"**Goal:** {goal}",
        f"**Outcome:** {'RESOLVED' if verdict.accepted else 'UNRESOLVED'}",
        f"**Reason:** {verdict.reason}",
        "",
    ]

    if understanding is not None:
        lines += [
            "## Understanding",
            f"- task_type: {understanding.task_type}",
            f"- acceptance_criteria: {understanding.acceptance_criteria}",
            "",
        ]

    if plan is not None:
        lines += ["## Plan", "```", plan.as_text(), "```", ""]

    repro = (evidence or {}).get("repro")
    if repro:
        mark = lambda ok: "yes" if ok else "NO"  # noqa: E731
        lines += [
            "## Reproduction (verified by Raven, not the model)",
            f"- test: `{repro.get('test')}`",
            f"- fails on the original code: {mark(repro.get('failed_before'))}",
            f"- passes on the fix: {mark(repro.get('passes_after'))}",
            "",
        ]

    lines += [
        "## Evidence",
        "```json",
        json.dumps(evidence, indent=2) if evidence is not None else "null",
        "```",
        "",
    ]

    if trace_text:
        lines += ["## Execution story", "```", trace_text, "```", ""]

    if sbfl_results:
        lines += ["## Suspicious locations (Ochiai)"]
        lines += [f"- {score}  {location}" for location, score in sbfl_results]
        lines += [""]

    lines += ["## Files changed"]
    lines += [f"- {p}" for p in executor_result.touched_paths] or ["(none)"]

    lines += [
        "",
        "## Usage",
        f"- tool calls: {executor_result.tool_calls}",
        f"- LLM calls: {gateway_stats.calls}",
        f"- tokens: {gateway_stats.total_tokens}",
    ]

    report_path.write_text("\n".join(lines))
    return report_path


def print_result_block(*, verdict: Verdict, executor_result, evidence: dict | None, gateway_stats: GatewayStats) -> None:
    print("===== RAVEN RESULT =====")
    print(f"outcome: {'RESOLVED' if verdict.accepted else 'UNRESOLVED'}")
    print(f"reason: {verdict.reason}")
    print(f"files changed: {', '.join(executor_result.touched_paths) or '(none)'}")
    if evidence is not None:
        print(f"evidence score: {evidence.get('evidence_score')}")
    print(f"tool calls: {executor_result.tool_calls}  ·  tokens: {gateway_stats.total_tokens}")
    print("===== END =====")
