"""Offline reflective prompt evolution (plan §12.3): a small evolutionary
search over prompts/base.yaml's modules, using the eval harness
(evals/run_evals.py) as the fitness function on (resolve rate, tokens).

# HONESTY NOTE -- read before trusting any output of this module.
#
# This repo has no live API key in this development environment. The eval
# harness's tasks script FakeClient with an EXACT, hardcoded tool-call
# sequence per task -- a task's outcome is entirely determined by its
# scripted responses, not by any system prompt text reaching the model.
# Running this loop offline exercises and proves the MECHANISM end to end
# (selection, mutation via a real REFLECT model call, crossover, held-out
# gating, versioned output) but is structurally incapable of finding a
# genuine improvement, because FakeClient cannot respond differently to
# different prompts. The held-out gate below will therefore correctly
# refuse to promote anything when run this way -- that is the correct,
# honest outcome, not a bug. A genuine tuning run needs `make evolve` with
# AI_API_KEY set to a real model and real budget. See README's
# "Learning & evolution" section.
"""

from __future__ import annotations

import argparse
import copy
import random
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from raven.core.json_utils import extract_fenced  # noqa: E402
from raven.llm.protocol import Message  # noqa: E402
from raven.prompts import load_prompts  # noqa: E402


def _prompt_targets():
    """Lazily imported so this module can be imported (e.g. by tests of
    just the selection/crossover logic) without pulling in the whole
    orchestrator stack at collection time."""
    import raven.agents.debugger as debugger_mod
    import raven.agents.explorer as explorer_mod
    import raven.agents.reviewer as reviewer_mod
    import raven.context.engine as engine_mod
    import raven.core.planner as planner_mod
    import raven.core.understand as understand_mod
    import raven.session.manager as session_mod

    return {
        "understand_system": (understand_mod, "UNDERSTAND_SYSTEM_PROMPT"),
        "plan_system": (planner_mod, "PLAN_SYSTEM_PROMPT"),
        "reviewer_system": (reviewer_mod, "REVIEWER_SYSTEM_PROMPT"),
        "debugger_hypotheses": (debugger_mod, "HYPOTHESES_PROMPT"),
        "debugger_verdict": (debugger_mod, "VERDICT_PROMPT"),
        "explorer_goal_prefix": (explorer_mod, "EXPLORER_GOAL_PREFIX"),
        "protocol_instructions": (engine_mod, "PROTOCOL_INSTRUCTIONS"),
        "answer_mode_instructions": (engine_mod, "ANSWER_MODE_INSTRUCTIONS"),
        "chat_system": (session_mod, "CHAT_SYSTEM_PROMPT"),
    }


@contextmanager
def apply_candidate(candidate: dict[str, str]):
    """Monkeypatches every prompt-consuming module's constant to the
    candidate's text for the duration of the `with` block, then restores
    the originals. Needed because raven/prompts.py's get_prompt() is read
    once, at import time, into plain module-level string constants (see
    e.g. raven/core/understand.py) -- writing prompts.tuned.yaml to disk
    after those modules are already imported has no runtime effect on its
    own; this is what actually makes a candidate's text take effect for an
    eval run."""
    targets = _prompt_targets()
    originals = {}
    for name, (module, attr) in targets.items():
        originals[name] = getattr(module, attr)
        if name in candidate:
            text = candidate[name]
            if name == "explorer_goal_prefix":
                text = text + " "  # matches raven/agents/explorer.py's original formatting
            setattr(module, attr, text)
    try:
        yield
    finally:
        for name, (module, attr) in targets.items():
            setattr(module, attr, originals[name])


def base_candidate() -> dict[str, str]:
    return dict(load_prompts())


@dataclass
class EvalScore:
    resolved: int
    total: int
    avg_tokens: float
    failed_tasks: list[dict] = field(default_factory=list)

    @property
    def resolve_rate(self) -> float:
        return self.resolved / self.total if self.total else 0.0


def evaluate_candidate(candidate: dict[str, str], task_paths: list[Path]) -> EvalScore:
    from evals.run_evals import run_task  # deferred: pulls in the orchestrator stack

    with apply_candidate(candidate):
        rows = [run_task(p) for p in task_paths]

    resolved = sum(1 for r in rows if r["resolved"])
    avg_tokens = sum(r["tokens"] for r in rows) / len(rows) if rows else 0.0
    failed = [r for r in rows if not r["resolved"]]
    return EvalScore(resolved=resolved, total=len(rows), avg_tokens=avg_tokens, failed_tasks=failed)


# Cheap, honestly-labeled heuristic (not a real learned credit-assignment
# model): maps a failed task's declared type to the prompt module most
# plausibly responsible, per plan §12.3's "tag with the state where it
# went wrong, mutate that module".
_TASK_TYPE_TO_MODULE = {
    "bug_fix": "understand_system", "feature": "plan_system", "refactor": "plan_system",
    "test_writing": "plan_system", "question": "answer_mode_instructions", "review": "reviewer_system",
}


def pick_module_to_mutate(failed_tasks: list[dict], rng: random.Random) -> str:
    if not failed_tasks:
        return rng.choice(list(_prompt_targets().keys()))
    candidates = [_TASK_TYPE_TO_MODULE.get(t.get("task_type", ""), "protocol_instructions") for t in failed_tasks]
    return rng.choice(candidates)


REFLECT_MUTATE_SYSTEM = """\
You are Raven's offline prompt optimizer. You are given the CURRENT text of
one prompt module and a summary of tasks that failed with it. Diagnose what
likely went wrong and propose a minimally-edited revision that keeps what
works and fixes the likely cause. Respond with exactly one fenced block:

```prompt
<the full revised prompt text>
```
"""


def reflect_and_mutate(gateway, module_name: str, current_text: str, failed_tasks: list[dict]) -> str:
    """One model call: diagnose + revise. Falls back to the unchanged text
    on any parse failure or model error -- a broken mutation must not
    corrupt the population, just produce a no-op child."""
    failures_text = "\n".join(f"- {t['id']} ({t.get('reason', '')})" for t in failed_tasks[:5]) or "(none)"
    task_text = f"Module: {module_name}\n\nCurrent text:\n{current_text}\n\nFailed tasks:\n{failures_text}"
    try:
        completion = gateway.complete(
            [Message(role="system", content=REFLECT_MUTATE_SYSTEM), Message(role="user", content=task_text)]
        )
        revised = extract_fenced(completion.text, "prompt")
        return revised.strip() if revised else current_text
    except Exception:
        return current_text


def crossover(parent_a: dict[str, str], parent_b: dict[str, str], rng: random.Random) -> dict[str, str]:
    return {name: (parent_a[name] if rng.random() < 0.5 else parent_b[name]) for name in parent_a}


def pareto_select(scored: list[tuple[dict, EvalScore]], keep: int) -> list[tuple[dict, EvalScore]]:
    """Non-dominated sort on (resolve rate desc, tokens asc). Simple O(n^2)
    dominance check -- population sizes here are single digits by design,
    so this doesn't need to be clever."""
    def dominates(a: EvalScore, b: EvalScore) -> bool:
        better_or_equal = a.resolve_rate >= b.resolve_rate and a.avg_tokens <= b.avg_tokens
        strictly_better = a.resolve_rate > b.resolve_rate or a.avg_tokens < b.avg_tokens
        return better_or_equal and strictly_better

    front = [
        (cand, score) for cand, score in scored
        if not any(dominates(other_score, score) for _, other_score in scored)
    ]
    front.sort(key=lambda pair: (-pair[1].resolve_rate, pair[1].avg_tokens))
    if len(front) >= keep:
        return front[:keep]
    return front + scored[: max(0, keep - len(front))]


@dataclass
class EvolutionResult:
    promoted: bool
    reason: str
    best_candidate: dict[str, str]
    baseline_holdout_score: EvalScore
    best_holdout_score: EvalScore
    generations_run: int
    output_path: Path | None = None


def run_evolution(
    gateway, generations: int = 2, population: int = 3, seed: int = 7,
    train_fraction: float = 0.75, out_path: Path | None = None,
) -> EvolutionResult:
    from evals.run_evals import TASKS_DIR

    rng = random.Random(seed)
    task_paths = sorted(TASKS_DIR.glob("*.yaml"))
    split = max(1, int(len(task_paths) * train_fraction))
    train_paths = task_paths[:split]
    holdout_paths = task_paths[split:] or task_paths[-1:]  # always at least 1 held-out task

    base = base_candidate()
    population_candidates = [copy.deepcopy(base) for _ in range(population)]
    best_candidate, best_train_score = base, evaluate_candidate(base, train_paths)

    for _gen in range(generations):
        scored = [(cand, evaluate_candidate(cand, train_paths)) for cand in population_candidates]
        survivors = pareto_select(scored, keep=max(1, population // 2))

        children = []
        for cand, score in survivors:
            children.append(cand)  # elitism: keep the parent
            module = pick_module_to_mutate(score.failed_tasks, rng)
            child = copy.deepcopy(cand)
            child[module] = reflect_and_mutate(gateway, module, cand[module], score.failed_tasks)
            children.append(child)
            other_parents = [c for c, _ in survivors if c is not cand]
            if other_parents and rng.random() < 0.3:
                children.append(crossover(cand, rng.choice(other_parents), rng))

        population_candidates = children[:population] or population_candidates

        gen_best_cand, gen_best_score = max(scored, key=lambda pair: (pair[1].resolve_rate, -pair[1].avg_tokens))
        if (gen_best_score.resolve_rate, -gen_best_score.avg_tokens) > (best_train_score.resolve_rate, -best_train_score.avg_tokens):
            best_candidate, best_train_score = gen_best_cand, gen_best_score

    # Held-out evaluation (plan §12.3 guard): these tasks were never used
    # for selection above, so this is the only number that can justify
    # promoting a candidate.
    baseline_holdout = evaluate_candidate(base, holdout_paths)
    best_holdout = evaluate_candidate(best_candidate, holdout_paths)

    improved = best_holdout.resolved > baseline_holdout.resolved  # minimum-gain gate: at least 1 task
    if not improved:
        return EvolutionResult(
            promoted=False,
            reason=(
                f"no candidate beat the baseline on held-out tasks (baseline "
                f"{baseline_holdout.resolved}/{baseline_holdout.total}, best "
                f"{best_holdout.resolved}/{best_holdout.total}). Expected when run "
                "offline -- see this module's HONESTY NOTE. Mechanism verified; "
                "no tuned prompts written."
            ),
            best_candidate=base, baseline_holdout_score=baseline_holdout,
            best_holdout_score=best_holdout, generations_run=generations,
        )

    out_path = out_path or (REPO_ROOT / "prompts.tuned.yaml")
    _write_tuned_yaml(out_path, best_candidate)
    return EvolutionResult(
        promoted=True,
        reason=(
            f"held-out improvement: {best_holdout.resolved}/{best_holdout.total} vs "
            f"baseline {baseline_holdout.resolved}/{baseline_holdout.total}"
        ),
        best_candidate=best_candidate, baseline_holdout_score=baseline_holdout,
        best_holdout_score=best_holdout, generations_run=generations, output_path=out_path,
    )


def _write_tuned_yaml(path: Path, candidate: dict[str, str]) -> None:
    data = {name: {"version": "tuned", "text": text} for name, text in candidate.items()}
    path.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="raven.learn.evolve")
    parser.add_argument("--generations", type=int, default=2)
    parser.add_argument("--population", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    from raven.config import load_config
    from raven.llm.fake import FakeClient
    from raven.llm.gateway import LLMGateway
    from raven.llm.providers import OpenAICompatibleClient

    config = load_config()
    if config.llm.api_key:
        client = OpenAICompatibleClient(
            base_url=config.llm.base_url, api_key=config.llm.api_key, model=config.llm.model,
        )
    else:
        print(
            "[warn] AI_API_KEY not set -- running the evolution MECHANISM offline "
            "against FakeClient. This cannot find a real improvement (see this "
            "module's HONESTY NOTE docstring). Set AI_API_KEY for a genuine run."
        )
        client = FakeClient(scripted_responses=["```prompt\n(unchanged -- FakeClient demo run)\n```"] * 500)
    gateway = LLMGateway(client)

    result = run_evolution(gateway, generations=args.generations, population=args.population, seed=args.seed)
    print(f"promoted: {result.promoted}")
    print(f"reason: {result.reason}")
    if result.output_path:
        print(f"written: {result.output_path}")
    gateway.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
