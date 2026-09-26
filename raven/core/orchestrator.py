"""The orchestrator (plan §6.1): a small state machine.

  single_loop:    INTAKE -> DIGEST -> EXECUTE -> VERIFY -> JUDGE -> FINALIZE
  plan_execute:   INTAKE -> UNDERSTAND -> DIGEST -> PLAN -> EXECUTE(per step)
                  -> VERIFY -> JUDGE -> (REPLAN -> PLAN)* -> FINALIZE
  delegated:      same shape as plan_execute, plus: steps that look
                  exploratory get delegated to the Explorer sub-agent
                  first (plan §7.3), and an accepted run gets one Reviewer
                  pass on the final diff before finalizing.

VERIFY always runs regardless of strategy if the repo has a detected test
command — that's how single_loop still gets an evidence score without
needing the Understanding/Planner machinery.

# PLAN-DECISION: `delegated` is implemented and tested, but config.yaml's
# executor.strategy default stays `single_loop` — same call already made
# once for plan_execute vs single_loop in Phase 1. Promoting a strategy
# needs a real ablation against the prescribed model; FakeClient-scripted
# evals can prove the code path works but can't produce a genuine
# cost/quality signal to justify flipping the default.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from raven.agents.explorer import explore, should_delegate_to_explorer
from raven.agents.reviewer import review_diff
from raven.core.executor import ExecutorResult, run_single_loop
from raven.core.judge import Verdict, judge
from raven.core.planner import Plan, make_plan
from raven.core.understand import Understanding, understand
from raven.learn.extract_lessons import extract_and_store_lesson
from raven.learn.reflect import RunReflection
from raven.llm.gateway import LLMGateway
from raven.memory.lessons import format_lesson, retrieve_combined
from raven.memory.project import record_fact
from raven.recovery.checkpoints import CheckpointManager
from raven.repo.digest import build_digest
from raven.report.report import write_report
from raven.tools.registry import RunContext, build_default_registry
from raven.verify.behavior_diff import diff_zero_arg_functions, find_collateral_changes
from raven.verify.reproduce import capture_post_fix, capture_pre_fix
from raven.verify.sbfl import run_sbfl
from raven.verify.score import compute_evidence
from raven.verify.trace import trace_test


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
    checkpoints: CheckpointManager = None  # the CheckpointManager used this run,
    # exposed so a caller (SessionManager's /undo) can restore_to_clean() after
    # the fact even though the run itself already finished.


def _new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _trace_and_localize(repo_root, pre):
    """Best-effort execution story + SBFL (plan §11 step 3), computed from
    `pre.failed` *before* the executor makes any edits — tracing/localizing
    against the original failure, not whatever state the repo happens to be
    in afterward (which may already be fixed). Purely informational: never
    affects the verdict, and any failure here degrades to empty/None rather
    than raising."""
    failing = pre.failed if pre is not None else []
    if not failing:
        return None, []

    try:
        trace_text = trace_test(repo_root, failing[0])
    except Exception:
        trace_text = None
    try:
        sbfl_results = run_sbfl(repo_root, failing[:3])
    except Exception:
        sbfl_results = []
    return trace_text, sbfl_results


def _collateral_changes(repo_root, checkpoints, understanding):
    """Best-effort behavioral diff (plan §11 step 5), computed after the
    executor has run, comparing each touched file's pre/post source. Only
    attempted when we know the intended target function
    (understanding.entities.symbols) — without one we can't distinguish
    "intended change" from "collateral change", so we skip rather than risk
    a false penalty. Only single-file, all-Python edits are considered;
    anything else is too ambiguous to diff this simply."""
    touched = checkpoints.touched_paths
    target = (understanding.entities.get("symbols") or [None])[0] if understanding else None
    if not target or len(touched) != 1 or not touched[0].endswith(".py"):
        return []

    try:
        path = touched[0]
        pre_source = checkpoints.original_content(path)
        post_source = (repo_root / path).read_text() if (repo_root / path).exists() else None
        if pre_source is None or post_source is None:
            return []
        changes = diff_zero_arg_functions(pre_source, post_source)
        return find_collateral_changes(changes, target)
    except Exception:
        return []


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
    checkpoints: CheckpointManager | None = None,
    approve_fn=None,
    understanding: Understanding | None = None,
    plan: Plan | None = None,
    on_event=None,
) -> OrchestratorResult:
    """`understanding`/`plan`, if supplied, come from a prior `/plan` the user
    already reviewed (plan §3: Plan mode "waits for approval or edits") —
    they're used as-is for the first pass instead of recomputing, so what you
    approved is what actually runs. Only a REPLAN (the verdict rejects the
    first pass) calls make_plan() again, since the approved plan demonstrably
    didn't work."""
    run_id = _new_run_id()
    registry = build_default_registry()
    ctx = RunContext(repo_root=repo_root, mode=mode, approve_fn=approve_fn)
    checkpoints = checkpoints or CheckpointManager(repo_root)

    def _emit(event, data):
        if on_event is None:
            return
        try:
            on_event(event, data)
        except Exception:
            pass

    # DIGEST
    digest = build_digest(repo_root)
    digest_summary = digest.summary()
    if digest.test_command:
        record_fact(repo_root, f"test command: {digest.test_command}")

    # Learning loop (plan §12): cross-task lessons retrieved once up front
    # and pinned for the whole run; in-run reflection (capped) accumulates
    # as steps fail and is re-pinned alongside them on every later call.
    pinned_lessons = [format_lesson(l) for l in retrieve_combined(repo_root, goal, k=3)]
    reflection = RunReflection()

    # UNDERSTAND (plan_execute / delegated only) — skip if the caller already
    # has one (e.g. SessionManager's /plan computed and showed it already).
    if strategy in ("plan_execute", "delegated") and understanding is None:
        understanding = understand(gateway, goal)

    # VERIFY baseline is captured before any edits, for either strategy
    _emit("verify_start", {"phase": "baseline"})
    pre = capture_pre_fix(registry, ctx, digest, understanding)
    # trace/SBFL localize the *original* failure — must run before any edit
    trace_text, sbfl_results = _trace_and_localize(repo_root, pre)

    if strategy in ("plan_execute", "delegated"):
        executor_result, plan, verdict, evidence = _run_plan_execute(
            gateway, registry, ctx, checkpoints, goal, understanding, digest_summary,
            max_replans, max_iterations, half_life, pre_evidence=pre, digest=digest, repo_root=repo_root,
            delegate=(strategy == "delegated"), initial_plan=plan,
            pinned_lessons=pinned_lessons, reflection=reflection, on_event=on_event,
        )
    else:
        executor_result = run_single_loop(
            gateway, registry, ctx, checkpoints, goal,
            digest_summary=digest_summary, max_iterations=max_iterations, half_life=half_life,
            run_lessons=pinned_lessons, on_event=on_event,
        )
        if not executor_result.completed:
            reflection.maybe_reflect(gateway, executor_result.aborted_reason or "")
        post = capture_post_fix(registry, ctx, digest, understanding)
        collateral = _collateral_changes(repo_root, checkpoints, understanding)
        evidence = compute_evidence(pre, post, executor_result.completed, collateral_changes=collateral)
        verdict = judge(executor_result, evidence)
        _emit("verify_done", {"evidence": evidence})

    # Cross-task lesson extraction (plan §12.2) — best-effort, must never
    # affect the verdict already decided above.
    try:
        extract_and_store_lesson(gateway, repo_root, goal, executor_result, verdict, evidence)
    except Exception:
        pass

    run_dir = (runs_dir or (repo_root / ".raven" / "runs")) / run_id
    report_path = write_report(
        run_dir, run_id=run_id, goal=goal, understanding=understanding, plan=plan,
        executor_result=executor_result, evidence=evidence, verdict=verdict, gateway_stats=gateway.stats,
        trace_text=trace_text, sbfl_results=sbfl_results,
    )

    return OrchestratorResult(
        run_id=run_id, accepted=verdict.accepted, reason=verdict.reason,
        understanding=understanding, plan=plan, executor_result=executor_result,
        evidence=evidence, report_path=report_path, checkpoints=checkpoints,
    )


def _run_plan_execute(
    gateway, registry, ctx, checkpoints, goal, understanding, digest_summary,
    max_replans, max_iterations, half_life, pre_evidence, digest, repo_root=None,
    delegate: bool = False, delegate_min_reads: int = 4, initial_plan: Plan | None = None,
    pinned_lessons: list[str] | None = None, reflection: RunReflection | None = None,
    on_event=None,
):
    def _emit(event, data):
        if on_event is None:
            return
        try:
            on_event(event, data)
        except Exception:
            pass

    pinned_lessons = pinned_lessons or []
    reflection = reflection or RunReflection()
    replans_left = max_replans
    note = ""
    executor_result = None
    plan = None
    evidence = None
    verdict = None
    first_pass = True

    while True:
        if first_pass and initial_plan is not None:
            plan = initial_plan  # the plan the user already saw via /plan — don't recompute it
        else:
            plan = make_plan(gateway, understanding, digest_summary, note=note)
        first_pass = False

        for step in plan.steps:
            _emit("plan_step", {"id": step.id, "action": step.action, "status": "active"})
            explorer_note = ""
            if delegate and should_delegate_to_explorer(step.action, delegate_min_reads):
                finding = explore(gateway, repo_root, step.action, digest_summary)
                explorer_note = f"\n\nExplorer findings:\n{finding}"

            step_goal = (
                f"{goal}\n\nCurrent plan step ({step.id}/{len(plan.steps)}): {step.action}\n"
                f"Done when: {step.check}{explorer_note}"
            )
            executor_result = run_single_loop(
                gateway, registry, ctx, checkpoints, step_goal,
                digest_summary=digest_summary, plan_text=plan.as_text(),
                max_iterations=max_iterations, half_life=half_life,
                run_lessons=pinned_lessons + reflection.lessons, on_event=on_event,
            )
            step.status = "done" if executor_result.completed else "failed"
            _emit("plan_step", {"id": step.id, "action": step.action, "status": step.status})
            if not executor_result.completed:
                reflection.maybe_reflect(gateway, executor_result.aborted_reason or "")
                break

        post = capture_post_fix(registry, ctx, digest, understanding)
        collateral = _collateral_changes(repo_root, checkpoints, understanding) if repo_root else []
        evidence = compute_evidence(pre_evidence, post, executor_result.completed, collateral_changes=collateral)
        verdict = judge(executor_result, evidence)

        if verdict.accepted or replans_left <= 0:
            break
        reflection.maybe_reflect(gateway, f"replanning: {verdict.reason}")
        replans_left -= 1
        note = verdict.reason

    if delegate and verdict.accepted and repo_root:
        # Reviewer runs once per task, only on an accepted, non-trivial diff
        # (plan §7.3 cost control — review_diff itself skips trivial diffs).
        review_note = review_diff(gateway, repo_root, evidence)
        verdict = Verdict(accepted=verdict.accepted, reason=f"{verdict.reason} | review: {review_note[:200]}")

    return executor_result, plan, verdict, evidence
