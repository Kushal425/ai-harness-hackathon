"""Offline numeric config tuning (plan §12.3): a small successive-halving
sweep over run_orchestrator's numeric knobs (half_life, max_replans,
max_iterations), using the eval harness as the fitness function.

# HONESTY NOTE -- same caveat as raven/learn/evolve.py: the eval harness's
# FakeClient-scripted tasks have a fixed, hardcoded tool-call sequence per
# task and cannot behave differently under different budgets or thresholds.
# This proves the sweep mechanism runs and picks a winner deterministically;
# it is not a genuine tuning signal. A real sweep needs a live model.
#
# Separately: config.yaml's `context.half_life` / `recovery.stall_turns` /
# etc. are declared but NOT currently read by raven/core/orchestrator.py at
# call time -- its function-signature defaults are used unless a caller
# passes explicit kwargs (raven/session/manager.py and raven/cli.py
# currently don't). This sweep operates directly on those kwargs, not on
# config.yaml, and does not fix that wiring gap; noted here rather than
# silently pretending otherwise.
"""

from __future__ import annotations

import argparse
import itertools
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Deliberately small: each combo re-runs the eval subset, and every task
# shells out to pytest at least once (baseline capture) -- a bigger grid is
# easy to blow the runtime budget on for what is, per the HONESTY NOTE
# above, a mechanism demo. Pass a custom `grid` to successive_halving() for
# a real (still offline-limited) sweep.
KNOB_GRID = {
    "half_life": [2, 5],
    "max_replans": [1, 3],
}


@dataclass
class SweepResult:
    best_knobs: dict
    best_resolved: int
    total: int
    all_results: list[dict] = field(default_factory=list)


def _score(knobs: dict, task_paths: list[Path]) -> int:
    import yaml

    from evals.run_evals import TOY_REPO, WORK_DIR, score_task
    from raven.core.orchestrator import run_orchestrator
    from raven.llm.fake import FakeClient
    from raven.llm.gateway import LLMGateway

    resolved = 0
    for task_path in task_paths:
        task = yaml.safe_load(task_path.read_text())
        repo_dir = WORK_DIR / f"tune_{task['id']}"
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        shutil.copytree(TOY_REPO, repo_dir)

        gateway = LLMGateway(FakeClient(scripted_responses=list(task["scripted_responses"])))
        result = run_orchestrator(
            gateway, repo_dir, goal=task["goal"], strategy=task.get("strategy", "single_loop"), **knobs
        )
        ok, _ = score_task(task.get("check", {}), repo_dir, result)
        if ok:
            resolved += 1
    return resolved


def successive_halving(task_paths: list[Path], grid: dict | None = None) -> SweepResult:
    grid = grid or KNOB_GRID
    combos = [dict(zip(grid.keys(), values)) for values in itertools.product(*grid.values())]

    # Start every combo on a small task subset, keep the top half, double
    # the subset, repeat, until one round covers every task. With this
    # repo's small eval set this converges in 1-2 rounds -- the structure
    # is what's being demonstrated, not a claim about search efficiency at
    # scale.
    subset_size = max(1, len(task_paths) // 4)
    surviving = combos
    all_results: list[dict] = []

    while True:
        subset = task_paths[:subset_size]
        scored = [(knobs, _score(knobs, subset)) for knobs in surviving]
        all_results.extend({"knobs": k, "subset_resolved": s, "subset_size": len(subset)} for k, s in scored)
        scored.sort(key=lambda pair: pair[1], reverse=True)
        surviving = [k for k, _ in scored[: max(1, len(scored) // 2)]]
        if subset_size >= len(task_paths) or len(surviving) <= 1:
            break
        subset_size = min(len(task_paths), subset_size * 2)

    final_scores = [(knobs, _score(knobs, task_paths)) for knobs in surviving]
    final_scores.sort(key=lambda pair: pair[1], reverse=True)
    best_knobs, best_resolved = final_scores[0]
    return SweepResult(best_knobs=best_knobs, best_resolved=best_resolved, total=len(task_paths), all_results=all_results)


def main(argv: list[str] | None = None) -> int:
    from evals.run_evals import TASKS_DIR

    parser = argparse.ArgumentParser(prog="raven.learn.tune")
    parser.parse_args(argv)

    task_paths = sorted(TASKS_DIR.glob("*.yaml"))
    result = successive_halving(task_paths)
    print(f"best knobs: {result.best_knobs}")
    print(f"resolved: {result.best_resolved}/{result.total}")
    print(
        "HONESTY NOTE: FakeClient-scripted eval tasks are config-invariant; "
        "this demonstrates the sweep mechanism, not a real tuning signal."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
