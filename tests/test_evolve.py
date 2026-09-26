from pathlib import Path

from raven.core.json_utils import extract_fenced
from raven.learn.evolve import (
    EvalScore,
    apply_candidate,
    base_candidate,
    crossover,
    pareto_select,
    reflect_and_mutate,
    run_evolution,
)
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway


def test_base_candidate_matches_loaded_prompts():
    cand = base_candidate()
    assert "plan_system" in cand
    assert "```plan" in cand["plan_system"]


def test_apply_candidate_patches_and_restores():
    import raven.core.planner as planner_mod

    original = planner_mod.PLAN_SYSTEM_PROMPT
    candidate = base_candidate()
    candidate["plan_system"] = "TEMPORARY TEST PROMPT"

    with apply_candidate(candidate):
        assert planner_mod.PLAN_SYSTEM_PROMPT == "TEMPORARY TEST PROMPT"

    assert planner_mod.PLAN_SYSTEM_PROMPT == original


def test_apply_candidate_restores_even_on_exception():
    import raven.core.planner as planner_mod

    original = planner_mod.PLAN_SYSTEM_PROMPT
    candidate = base_candidate()
    candidate["plan_system"] = "SHOULD BE ROLLED BACK"
    try:
        with apply_candidate(candidate):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert planner_mod.PLAN_SYSTEM_PROMPT == original


def test_reflect_and_mutate_returns_revised_text():
    resp = '```prompt\nREVISED TEXT HERE\n```'
    gateway = LLMGateway(FakeClient(scripted_responses=[resp]))
    revised = reflect_and_mutate(gateway, "plan_system", "old text", [{"id": "t1", "reason": "failed"}])
    assert revised == "REVISED TEXT HERE"


def test_reflect_and_mutate_falls_back_to_current_text_on_bad_response():
    gateway = LLMGateway(FakeClient(scripted_responses=["no fenced block here"]))
    revised = reflect_and_mutate(gateway, "plan_system", "old text", [])
    assert revised == "old text"


def test_crossover_only_picks_from_parents(monkeypatch):
    import random
    a = {"m1": "A1", "m2": "A2"}
    b = {"m1": "B1", "m2": "B2"}
    child = crossover(a, b, random.Random(0))
    for key, val in child.items():
        assert val in (a[key], b[key])


def test_pareto_select_prefers_higher_resolve_rate_and_lower_tokens():
    good = ({"x": "good"}, EvalScore(resolved=10, total=10, avg_tokens=100))
    bad = ({"x": "bad"}, EvalScore(resolved=5, total=10, avg_tokens=500))
    front = pareto_select([good, bad], keep=1)
    assert front[0][0] == {"x": "good"}


def test_pareto_select_pads_with_extras_if_front_too_small():
    # Only one candidate exists at all -- padding to keep=2 duplicates it
    # rather than crashing or silently returning fewer than requested.
    only = ({"x": "only"}, EvalScore(resolved=1, total=1, avg_tokens=1))
    front = pareto_select([only], keep=2)
    assert len(front) == 2
    assert all(cand == {"x": "only"} for cand, _ in front)


def test_run_evolution_completes_offline_and_does_not_promote(tmp_path):
    """Mechanism smoke test (see raven/learn/evolve.py's HONESTY NOTE):
    FakeClient-scripted eval tasks are prompt-invariant, so an offline run
    must never promote a candidate -- if it did, that would mean the
    held-out gate is broken, not that evolution "worked"."""
    gateway = LLMGateway(FakeClient(scripted_responses=["```prompt\n(demo)\n```"] * 200))
    result = run_evolution(
        gateway, generations=1, population=2, seed=1, out_path=tmp_path / "prompts.tuned.yaml"
    )
    assert result.promoted is False
    assert "held-out" in result.reason
    assert not (tmp_path / "prompts.tuned.yaml").exists()


def test_extract_fenced_prompt_tag_used_by_reflect_and_mutate():
    text = 'blah\n```prompt\nHELLO\n```\ntrailing'
    assert extract_fenced(text, "prompt").strip() == "HELLO"
