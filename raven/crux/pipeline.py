"""The Crux loop.

  intake+map (det) -> LOCALIZE (1 call) -> PROBE (1 call, run on original)
  -> CANDIDATES (k calls, parallel) -> EXECUTE each (probe + targeted tests)
  -> [no survivor: deterministic diagnosis -> feedback -> one more round]
  -> CRUX: run survivors on probe + variant + harvested inputs, cluster by
     behaviour, ask about the most informative disagreement (0-2 calls)
  -> SELECT (largest surviving cluster, smallest diff) -> APPLY + VALIDATE
  -> certificate.

Returns None when Crux can't apply (no Python code to localise); the
orchestrator then falls back to the ReAct executor, seeded with whatever
Crux learned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from raven.crux import candidates as cand_mod
from raven.crux.adjudicate import Verdict, adjudicate, gather_evidence
from raven.crux.disagree import best_crux, cluster, harvested_cases, variant_cases
from raven.crux.issue import build_issue_card
from raven.crux.llm import Ledger, ask_json
from raven.crux.patching import Candidate, applied
from raven.crux.probe import Case, Probe, ProbeRun, describe, run_probe
from raven.crux.regression import related_tests, run_tests
from raven.crux.repomap import FunctionInfo, RepoMap


@dataclass
class CruxOutcome:
    winner: Candidate | None
    best: Candidate | None                 # anytime best-so-far (may not be fully verified)
    candidates: list[Candidate]
    locations: list[dict]
    probe: Probe | None
    probe_before: ProbeRun | None
    probe_after: ProbeRun | None
    reproduced: bool
    baseline_failed: list[str]
    after_failed: list[str]
    tests_run: list[str]
    clusters: list[list[str]] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)
    inputs_tried: int = 0
    ledger: Ledger = field(default_factory=Ledger)
    notes: list[str] = field(default_factory=list)

    def hints(self) -> str:
        """What Crux learned, for the ReAct fallback's task card."""
        parts = []
        if self.locations:
            parts.append("Likely locations: " + "; ".join(f"{l['file']}::{l['symbol']} ({l.get('hypothesis', '')})"
                                                          for l in self.locations))
        if self.probe and self.probe_before:
            parts.append("Observed behaviour of the original code:\n" + describe(self.probe, self.probe_before))
        parts += self.notes[-4:]
        return "\n\n".join(parts)


def _working(on_event, text: str) -> None:
    """A slow step is starting (the UI shows a live spinner until the next event)."""
    if on_event:
        try:
            on_event("working", {"text": text})
        except Exception:
            pass


def _emit(on_event, stage: str, text: str, **data) -> None:
    if on_event:
        try:
            on_event("crux", {"stage": stage, "text": text, **data})
        except Exception:
            pass


def _locate(gateway, ledger, card, rmap: RepoMap, ranked: list[FunctionInfo]) -> list[tuple[dict, FunctionInfo]]:
    user = (f"ISSUE:\n{card.text.strip()[:4000]}\n\n{card.summary()}\n\n"
            f"RANKED FUNCTIONS:\n{rmap.skeletons(ranked, 18)}")
    data = ask_json(gateway, "crux_localize", user, ledger, "localize", required=("locations",)) or {}
    out: list[tuple[dict, FunctionInfo]] = []
    for loc in data.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        fn = rmap.find(str(loc.get("file", "")), str(loc.get("symbol", "")))
        if fn and all(fn is not f for _, f in out):
            out.append(({"file": fn.path, "symbol": fn.qualname,
                         "hypothesis": str(loc.get("hypothesis", ""))[:200]}, fn))
    if not out:  # the model localized nothing usable: fall back to the map's top functions
        for fn in ranked[:2]:
            if fn.score > 0:
                out.append(({"file": fn.path, "symbol": fn.qualname, "hypothesis": "ranked by the repo map"}, fn))
    # Only the model's own picks otherwise: unrelated "top-ups" sent the probe
    # and the candidates after functions the issue never mentions.
    return out[:3]


def _probe(gateway, ledger, card, rmap, fns, repo_root) -> tuple[Probe | None, ProbeRun | None]:
    code = "\n\n".join(rmap.span(f) for f in fns[:2])
    user = f"ISSUE:\n{card.text.strip()[:4000]}\n\nRELEVANT CODE:\n{code}"
    for attempt in range(2):
        data = ask_json(gateway, "crux_probe", user, ledger, "probe", required=("cases",)) or {}
        cases = [Case(expr=str(c["expr"]), expected=_valid_expr(c.get("expected")),
                      raises=_valid_name(c.get("raises")))
                 for c in data.get("cases") or [] if isinstance(c, dict) and _valid_expr(c.get("expr"))][:4]
        if not cases:
            continue
        relevant = {f.name for f in fns} | set(card.identifiers)
        cases = [c for c in cases if _calls(c.expr) & relevant] or cases[:1]
        probe = Probe(setup=str(data.get("setup") or ""), cases=cases)
        run = run_probe(repo_root, probe)
        if not run.setup_error:
            return probe, run
        user += f"\n\nYOUR PREVIOUS PROBE FAILED TO IMPORT: {run.setup_error}\nFix the setup."
    return None, None


def _drop_contradictions(probe: Probe, run: ProbeRun, issue: str, repo_root) -> tuple:
    """An expectation equal to what the ORIGINAL code already returns, for a
    call the issue itself quotes, contradicts the issue (the model copied
    the reported buggy output as 'expected'). Drop it rather than let it
    reject every correct fix."""
    flat = " ".join(issue.split())
    changed = False
    for case, res in zip(probe.cases, run.results):
        if case.expected is not None and res.match and " ".join(case.expr.split()) in flat:
            case.expected, changed = None, True
    return (probe, run_probe(repo_root, probe)) if changed else (probe, run)


def _guard_cases(probe: Probe | None, rmap: RepoMap, fns) -> list:
    base = list(probe.cases) if probe else []
    return [Case(c.expr, None, c.raises, c.setup, c.origin) for c in base] + \
        variant_cases(Probe("", base)) + harvested_cases(rmap, {f.name for f in fns})


def _new_crashes(guard: Probe | None, before: ProbeRun | None, after: ProbeRun | None) -> list:
    """Inputs where the original code returned a value but the candidate
    raises -- unless the issue asked for that exception."""
    if not (guard and before and after):
        return []
    out = []
    for case, b, a in zip(guard.cases, before.results, after.results):
        if (b.outcome or "").startswith("=> ") and (a.outcome or "").startswith("raises ") and not case.raises:
            out.append(f"{a.outcome} on {case.expr} (original: {b.outcome[3:60]})")
    return out


def _calls(expr: str) -> set:
    """Names of the functions/methods an expression calls."""
    import ast

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    return {n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
            for n in ast.walk(tree) if isinstance(n, ast.Call)}


def _valid_expr(value) -> str | None:
    """A Python expression, or None -- a model writing "should return 4"
    instead of `4` must not turn every candidate into a failure."""
    if value is None or not str(value).strip():
        return None
    try:
        compile(str(value), "<expected>", "eval")
    except SyntaxError:
        return None
    return str(value)


def _valid_name(value) -> str | None:
    name = str(value or "").strip().split(".")[-1].split("(")[0]
    return name if name.isidentifier() else None


def _improves(before: ProbeRun | None, after: ProbeRun) -> bool:
    """More of the issue's expectations hold than on the original code, and
    none that held before now fails. (Requiring ALL to hold let one wrong
    model-written expectation reject every correct fix.)"""
    if before is None:
        return after.matched > 0
    regressed = any(b.match and not a.match for b, a in zip(before.results, after.results))
    return after.matched > before.matched and not regressed


def _evaluate(repo_root, cand: Candidate, probe, reproduced, rmap, baseline_failed, base_tests,
              before: ProbeRun | None = None, guard: Probe | None = None, guard_before: ProbeRun | None = None,
              guard_runs: dict | None = None) -> tuple:
    """(probe_run, new_failures, tests). Sets cand.status."""
    tests = sorted(set(base_tests) | set(related_tests(rmap, list(cand.files))))
    with applied(repo_root, cand):
        run = run_probe(repo_root, probe) if probe else None
        ran, failed, tail = run_tests(repo_root, tests)
        guard_run = run_probe(repo_root, guard) if guard and guard.cases else None
    if guard_runs is not None and guard_run is not None:
        guard_runs[cand.id] = guard_run
    crashes = _new_crashes(guard, guard_before, guard_run)
    new_fail = sorted(set(failed) - set(baseline_failed))
    if not ran:
        cand.status = "broke-tests"
        cand.notes.append(f"test run failed to execute: {tail.splitlines()[-1:] or ''}")
    elif new_fail:
        cand.status = "broke-tests"
        cand.notes.append("new failures: " + ", ".join(new_fail[:5]))
    elif crashes:
        cand.status = "new-crash"
        cand.notes.append("; ".join(crashes[:3]))
    elif reproduced and run is not None and not _improves(before, run):
        cand.status = "misses-issue"
        cand.notes.append("probe still wrong:\n" + describe(probe, run))
    else:
        cand.status = "alive"
        if reproduced and run is not None and not run.all_expected_met:
            cand.notes.append("partial: fixes some but not all expected cases")
    return run, new_fail, tests


def _score(cand: Candidate, run: ProbeRun | None, new_fail: list) -> tuple:
    return (cand.status == "alive", run.matched if run else 0, -len(new_fail), -cand.size)


def run_crux(gateway, repo_root: Path, goal: str, *, budget=None, k: int = 2, max_candidates: int = 4,
             parallel: bool = True, on_event=None) -> CruxOutcome | None:
    repo_root = Path(repo_root).resolve()
    ledger = Ledger()
    card = build_issue_card(goal)
    rmap = RepoMap(repo_root)
    if not rmap.functions:
        return None
    ranked = rmap.rank(card)
    _emit(on_event, "map", f"indexed {len(rmap.functions)} functions, {len(rmap.test_files)} test files")

    _working(on_event, "reading the issue and locating the code…")
    located = _locate(gateway, ledger, card, rmap, ranked)
    if not located:
        return None
    locations = [l for l, _ in located]
    fns = [f for _, f in located]
    _emit(on_event, "localize", " · ".join(f"{l['file']}::{l['symbol']}" for l in locations))

    _working(on_event, "writing a probe that reproduces the issue…")
    probe, before = _probe(gateway, ledger, card, rmap, fns, repo_root)
    if probe and before:
        probe, before = _drop_contradictions(probe, before, card.text, repo_root)
    reproduced = bool(before and before.expectation_known and not before.all_expected_met)
    if probe:
        _emit(on_event, "probe", ("reproduced: " if reproduced else "not reproduced: ")
              + "; ".join(f"{c.expr} -> {r.outcome}" for c, r in zip(probe.cases, before.results))[:300])

    base_tests = related_tests(rmap, sorted({f.path for f in fns}))
    _ran, baseline_failed, _ = run_tests(repo_root, base_tests)
    # Behaviour guard: the issue's calls + variants + calls from the repo's
    # tests, run once on the original code; every candidate is checked for
    # NEW crashes on inputs the original handled (and the same run later
    # feeds the crux clustering).
    guard_cases = _guard_cases(probe, rmap, fns)
    guard = Probe(probe.setup if probe else "", guard_cases)
    guard_before = run_probe(repo_root, guard) if guard_cases else None

    out = CruxOutcome(winner=None, best=None, candidates=[], locations=locations, probe=probe,
                      probe_before=before, probe_after=None, reproduced=reproduced,
                      baseline_failed=baseline_failed, after_failed=[], tests_run=base_tests, ledger=ledger)
    code = "\n\n".join(rmap.span(f, numbered=False) for f in fns)
    observed = describe(probe, before) if probe else "(no executable reproduction)"
    feedback: list[str] = []
    runs: dict[str, ProbeRun | None] = {}
    guard_runs: dict[str, ProbeRun] = {}
    best_key = None

    for round_no in range(2):
        if budget is not None and budget.exhausted():
            break
        prefix = (f"ISSUE:\n{card.text.strip()[:4000]}\n\nCODE:\n{code}\n\n"
                  f"OBSERVED BEHAVIOUR OF THE ORIGINAL CODE:\n{observed}"
                  + ("\n\nWHAT FAILED IN EARLIER ATTEMPTS:\n" + "\n".join(feedback) if feedback else ""))
        n = k if round_no == 0 else min(2, max_candidates - len(out.candidates))
        if n <= 0:
            break
        _working(on_event, f"writing {n} candidate fix{'es' if n > 1 else ''}…")
        batch = cand_mod.generate(gateway, ledger, repo_root, prefix, locations, len(out.candidates), n, parallel)
        for cand in batch:
            out.candidates.append(cand)
            if cand.status == "invalid":
                _emit(on_event, "candidate", f"{cand.id} invalid: {cand.error}", id=cand.id, status="invalid")
                continue
            run, new_fail, tests = _evaluate(repo_root, cand, probe, reproduced, rmap, baseline_failed, base_tests,
                                             before, guard, guard_before, guard_runs)
            runs[cand.id] = run
            key = _score(cand, run, new_fail)
            if best_key is None or key > best_key:
                best_key, out.best = key, cand
            _emit(on_event, "candidate", f"{cand.id} {cand.status} ({cand.size} changed lines) — {cand.hypothesis}",
                  id=cand.id, status=cand.status, diff=cand.diff)
        alive = [c for c in out.candidates if c.status == "alive"]
        if alive and (len(alive) >= 2 or reproduced or len(out.candidates) >= max_candidates):
            break
        # -- diagnosis: why did the round fail? -> targeted feedback, not a restart
        for cand in batch:
            if cand.status == "invalid":
                feedback.append(f"- {cand.id}: edit could not be applied ({cand.error}).")
            elif cand.status == "broke-tests":
                feedback.append(f"- {cand.id} ({cand.hypothesis}) broke existing tests: {'; '.join(cand.notes)[:400]}")
            elif cand.status == "new-crash":
                feedback.append(f"- {cand.id} ({cand.hypothesis}) introduced crashes on inputs the original code "
                                f"handled: {'; '.join(cand.notes)[:400]}. Keep existing behaviour working.")
            elif cand.status == "misses-issue":
                unchanged = runs.get(cand.id) and before and runs[cand.id].fingerprint == before.fingerprint
                if unchanged:
                    feedback.append(f"- {cand.id} changed {', '.join(cand.files)} but the observed behaviour did not "
                                    "change at all: the cause is probably elsewhere.")
                else:
                    feedback.append(f"- {cand.id} ({cand.hypothesis}) changed behaviour but not to what the issue "
                                    f"expects: {'; '.join(cand.notes)[:400]}")
        if all(c.status == "misses-issue" and runs.get(c.id) and before and runs[c.id].fingerprint == before.fingerprint
               for c in batch if c.status != "invalid") and len(ranked) > 3:
            # wrong location: rotate in the next functions from the map
            tried = {l["symbol"] for l in locations}
            extra = [f for f in ranked if f.qualname not in tried][:2]
            locations += [{"file": f.path, "symbol": f.qualname, "hypothesis": "next-ranked location"} for f in extra]
            fns += extra
            code = "\n\n".join(rmap.span(f, numbered=False) for f in fns[:4])
        _emit(on_event, "adapt", feedback[-1] if feedback else "retrying")
        out.notes += feedback[-3:]

    alive = [c for c in out.candidates if c.status == "alive"]
    if not alive:
        return out
    # Prefer the survivors that satisfy the most of the issue's expectations.
    top = max((runs[c.id].matched if runs.get(c.id) else 0) for c in alive)
    for c in alive:
        if (runs[c.id].matched if runs.get(c.id) else 0) < top:
            c.status = "outscored"
            c.notes.append(f"fixes fewer expected cases than the best ({top})")
    alive = [c for c in alive if c.status == "alive"]

    # -- CRUX: where do the survivors actually disagree? --------------------
    setup = guard.setup
    cases = list(guard.cases)
    crux_probe = Probe(setup, [Case(c.expr, None, None, c.setup, c.origin) for c in cases])
    original_run = guard_before
    fps = {c.id: (guard_runs[c.id].fingerprint if c.id in guard_runs else ()) for c in alive}
    clusters = cluster(fps)
    out.inputs_tried = len(cases)

    if len(clusters) == 1 and len(alive) >= 2 and len({c.normalized() for c in alive}) > 1 and cases \
            and (budget is None or not budget.exhausted()):
        data = ask_json(gateway, "crux_inputs", f"SETUP:\n{setup}\n\nCODE:\n{code}\n\nDIFFS:\n" +
                        "\n".join(f"--- {c.id}\n{c.diff}" for c in alive[:3]), ledger, "inputs", required=("exprs",))
        extra = [Case(str(e), origin="model") for e in (data or {}).get("exprs", [])[:5] if isinstance(e, str)]
        if extra:
            cases += extra
            crux_probe = Probe(setup, [Case(c.expr, None, None, c.setup, c.origin) for c in cases])
            original_run = run_probe(repo_root, crux_probe)
            for cand in alive:
                with applied(repo_root, cand):
                    fps[cand.id] = run_probe(repo_root, crux_probe).fingerprint
            clusters = cluster(fps)
            out.inputs_tried = len(cases)
    out.clusters = [c.members for c in clusters]
    _emit(on_event, "cluster", f"{len(alive)} surviving candidates -> {len(clusters)} behaviour cluster(s) "
                               f"over {len(cases)} inputs", clusters=out.clusters)

    for _ in range(2):
        if len(clusters) < 2 or (budget is not None and budget.exhausted()):
            break
        idx = best_crux(clusters, cases)
        if idx is None:
            break
        case = cases[idx]
        evidence = gather_evidence(rmap, fns, case)
        orig = original_run.results[idx].outcome if original_run else None
        _emit(on_event, "crux", f"{case.expr}   " + "  vs  ".join(
            f"[{c.label}] {c.fingerprint[idx]}" for c in clusters))
        _working(on_event, f"asking one question: what should {case.expr} do?")
        verdict = adjudicate(gateway, ledger, card.text, code, case, idx, clusters, orig, evidence)
        out.verdicts.append(verdict)
        _emit(on_event, "verdict", f"{verdict.choice}: {verdict.because}", choice=verdict.choice)
        if verdict.unspecified:
            break
        kept = [c for c in clusters if verdict.keeps(c, idx)]
        clusters = kept or clusters

    # -- SELECT: largest surviving cluster, then the smallest diff ----------
    pool = [c for c in alive if c.id in set(clusters[0].members)] if clusters else alive
    out.winner = min(pool, key=lambda c: (c.size, c.id))
    for c in alive:
        if c.id not in {m for cl in clusters for m in cl.members}:
            c.status = "rejected-by-crux"
    _emit(on_event, "select", f"selected {out.winner.id} ({out.winner.size} changed lines)", diff=out.winner.diff)
    return out
