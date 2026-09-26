"""The certificate: what Crux can actually show for its choice, as the run's
evidence dict (report, TUI, result block) and a readable Markdown ledger.

Evidence score, from executed facts only:
  +0.4  reproduction fixed: the probe's expectations failed on the original
        code and all hold on the fix
  +0.2  no new failures in the tests that exercise the touched modules
  +0.2  agreement: every surviving candidate behaves identically on all
        inputs tried, or their disagreement was settled by an adjudicated crux
  +0.2  independent confirmation: at least two candidates survived
"""

from __future__ import annotations

from raven.crux.pipeline import CruxOutcome
from raven.crux.probe import ProbeRun, describe


def build_evidence(out: CruxOutcome, after: ProbeRun | None, after_failed: list[str], tests_ran: bool) -> dict:
    alive = [c for c in out.candidates if c.status in ("alive", "rejected-by-crux")]
    repro_fixed = bool(out.reproduced and after and after.all_expected_met) if out.reproduced else None
    no_new = not (set(after_failed) - set(out.baseline_failed))
    settled = len(out.clusters) == 1 or any(not v.unspecified for v in out.verdicts)
    score = 0.0
    score += 0.4 if repro_fixed else 0.0
    score += 0.2 if (tests_ran and no_new) else 0.0
    score += 0.2 if (settled and len(alive) >= 1) else 0.0
    score += 0.2 if len(alive) >= 2 else 0.0
    evidence = {
        "strategy": "crux",
        "evidence_score": round(score, 2),
        "repro_fixed": repro_fixed,
        "no_new_failures": no_new if tests_ran else None,
        "pre_failed": out.baseline_failed,
        "post_failed": after_failed,
        "collateral_changes": [],
        "crux": {
            "locations": out.locations,
            "reproduced": out.reproduced,
            "probe_before": describe(out.probe, out.probe_before) if out.probe and out.probe_before else None,
            "probe_after": describe(out.probe, after) if out.probe and after else None,
            "candidates": [{"id": c.id, "status": c.status, "lines": c.size, "hypothesis": c.hypothesis,
                            "notes": c.notes[:3]} for c in out.candidates],
            "inputs_tried": out.inputs_tried,
            "clusters": out.clusters,
            "verdicts": [{"input": v.expr, "original": v.original, "options": v.options, "choice": v.choice,
                          "because": v.because, "evidence": v.evidence[:6]} for v in out.verdicts],
            "winner": out.winner.id if out.winner else None,
            "tests_run": out.tests_run,
            "model_usage": {k: {"calls": s.calls, "tokens": s.tokens} for k, s in out.ledger.stages.items()},
        },
    }
    if out.reproduced:
        evidence["repro"] = {"test": "crux probe", "failed_before": True,
                             "passes_after": bool(after and after.all_expected_met), "verified": bool(repro_fixed)}
    return evidence


def reason(out: CruxOutcome, evidence: dict) -> str:
    alive = [c for c in out.candidates if c.status in ("alive", "rejected-by-crux")]
    parts = []
    if out.reproduced:
        parts.append("reproduction fixed" if evidence["repro_fixed"] else "reproduction NOT fixed")
    else:
        parts.append("issue not reproduced by a probe")
    if evidence["no_new_failures"] is not None:
        parts.append("no new test failures" if evidence["no_new_failures"] else "NEW test failures")
    parts.append(f"{len(out.candidates)} candidates, {len(alive)} survived, {len(out.clusters)} behaviour cluster(s)")
    for v in out.verdicts:
        parts.append(f"crux `{v.expr}` -> {v.choice}")
    return "; ".join(parts)


def markdown(out: CruxOutcome, evidence: dict) -> str:
    c = evidence["crux"]
    lines = ["## Crux ledger", ""]
    lines.append("**Locations:** " + "; ".join(f"`{l['file']}::{l['symbol']}` — {l.get('hypothesis', '')}"
                                             for l in c["locations"]))
    if c["probe_before"]:
        lines += ["", "**Probe on the original code** (reproduced: " + ("yes" if c["reproduced"] else "no") + ")",
                  "```", c["probe_before"], "```"]
    if c["probe_after"]:
        lines += ["**Probe on the selected fix**", "```", c["probe_after"], "```"]
    lines += ["", "| candidate | status | lines | hypothesis |", "|---|---|---|---|"]
    for cand in c["candidates"]:
        lines.append(f"| {cand['id']} | {cand['status']} | {cand['lines']} | {cand['hypothesis'][:90]} |")
    lines += ["", f"**Behaviour clusters** over {c['inputs_tried']} inputs: "
              + (" · ".join("{" + ", ".join(cl) + "}" for cl in c["clusters"]) or "(none)")]
    for v in c["verdicts"]:
        lines += ["", f"**Crux:** `{v['input']}` (original code: `{v['original']}`)"]
        for letter, opt in v["options"].items():
            mark = " ← chosen" if letter == v["choice"] else ""
            lines.append(f"- {letter}) `{opt['outcome']}` — {', '.join(opt['members'])}{mark}")
        lines.append(f"- verdict **{v['choice']}**: {v['because']}")
        if v["evidence"]:
            lines += ["- evidence:"] + [f"  - `{e}`" for e in v["evidence"]]
    lines += ["", f"**Selected:** {c['winner']}", "", "**Model usage by stage:** " + ", ".join(
        f"{k} {u['calls']} call(s)/{u['tokens']} tok" for k, u in c["model_usage"].items())]
    if out.winner:
        lines += ["", "**Patch**", "```diff", out.winner.diff.rstrip(), "```"]
    return "\n".join(lines)
