"""The orchestrator (plan §6.1): a small state machine.

  single_loop:    INTAKE -> DIGEST -> EXECUTE -> VERIFY -> JUDGE -> FINALIZE
  plan_execute:   INTAKE -> UNDERSTAND -> DIGEST -> PLAN -> EXECUTE(per step)
                  -> VERIFY -> JUDGE -> (REPLAN -> PLAN)* -> FINALIZE

VERIFY always runs regardless of strategy if the repo has a detected test
command — that's how single_loop still gets an evidence score without
needing the Understanding/Planner machinery.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from raven.core.executor import ExecutorResult, run_single_loop
from raven.core.judge import Verdict, judge
from raven.core.planner import Plan, make_plan
from raven.core.understand import Understanding, understand
from raven.llm.gateway import LLMGateway
from raven.recovery.checkpoints import CheckpointManager
from raven.repo.digest import build_digest
from raven.report.report import write_report
from raven.tools.registry import RunContext, build_default_registry
from raven.verify.reproduce import capture_post_fix, capture_pre_fix
from raven.verify.score import compute_evidence


@dataclass
class OrchestratorResult:
    run_id: str
    accepted: bool
    reason: str
    understanding: Understanding | None
    plan: Plan | None
    executor_result: ExecutorResult
    evidence: dict | None
    report_path: object


def _new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def run_orchestrator(
    gateway: LLMGateway,
    repo_root,
    goal: str,
    mode: str = "autonomous",
    strategy: str = "single_loop",
    max_replans: int = 2,
    max_iterations: int = 15,
    half_life: int = 3,
    runs_dir=None,
) -> OrchestratorResult:
    run_id = _new_run_id()
    registry = build_default_registry()
    ctx = RunContext(repo_root=repo_root, mode=mode)
    checkpoints = CheckpointManager(repo_root)

    # DIGEST
    digest = build_digest(repo_root)
    digest_summary = digest.summary()

    understanding: Understanding | None = None
    plan: Plan | None = None

    # UNDERSTAND (plan_execute only)
    if strategy == "plan_execute":
        understanding = understand(gateway, goal)

    # VERIFY baseline is captured before any edits, for either strategy
    pre = capture_pre_fix(registry, ctx, digest, understanding)

    if strategy == "plan_execute":
        executor_result, plan, verdict, evidence = _run_plan_execute(
            gateway, registry, ctx, checkpoints, goal, understanding, digest_summary,
            max_replans, max_iterations, half_life, pre_evidence=pre, digest=digest,
        )
    else:
        executor_result = run_single_loop(
            gateway, registry, ctx, checkpoints, goal,
            digest_summary=digest_summary, max_iterations=max_iterations, half_life=half_life,
        )
        post = capture_post_fix(registry, ctx, digest, understanding)
        evidence = compute_evidence(pre, post, executor_result.completed)
        verdict = judge(executor_result, evidence)

    run_dir = (runs_dir or (repo_root / ".raven" / "runs")) / run_id
    report_path = write_report(
        run_dir, run_id=run_id, goal=goal, understanding=understanding, plan=plan,
        executor_result=executor_result, evidence=evidence, verdict=verdict, gateway_stats=gateway.stats,
    )

    return OrchestratorResult(
        run_id=run_id, accepted=verdict.accepted, reason=verdict.reason,
        understanding=understanding, plan=plan, executor_result=executor_result,
        evidence=evidence, report_path=report_path,
    )


def _run_plan_execute(
    gateway, registry, ctx, checkpoints, goal, understanding, digest_summary,
    max_replans, max_iterations, half_life, pre_evidence, digest,
):
    replans_left = max_replans
    note = ""
    executor_result = None
    plan = None
    evidence = None
    verdict = None

    while True:
        plan = make_plan(gateway, understanding, digest_summary, note=note)

        for step in plan.steps:
            step_goal = (
                f"{goal}\n\nCurrent plan step ({step.id}/{len(plan.steps)}): {step.action}\n"
                f"Done when: {step.check}"
            )
            executor_result = run_single_loop(
                gateway, registry, ctx, checkpoints, step_goal,
                digest_summary=digest_summary, plan_text=plan.as_text(),
                max_iterations=max_iterations, half_life=half_life,
            )
            step.status = "done" if executor_result.completed else "failed"
            if not executor_result.completed:
                break

        post = capture_post_fix(registry, ctx, digest, understanding)
        evidence = compute_evidence(pre_evidence, post, executor_result.completed)
        verdict = judge(executor_result, evidence)

        if verdict.accepted or replans_left <= 0:
            break
        replans_left -= 1
        note = verdict.reason

    return executor_result, plan, verdict, evidence
