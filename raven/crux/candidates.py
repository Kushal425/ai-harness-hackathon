"""Candidate generation: k one-shot fix proposals from a shared,
cache-friendly prompt prefix (issue, code, probe observations, feedback),
differing only in a short suffix -- a different location hypothesis or
framing, plus temperature for the later ones. Diversity by hypothesis, not
only by sampling: at temperature 0 these models repeat themselves.

Calls run in parallel when allowed (latency ~ one call). An edit that
fails to apply gets exactly one repair call with the precise mismatch.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from raven.crux.llm import Ledger, ask_json
from raven.crux.patching import Candidate, Edit, build_candidate

FRAMINGS = [
    ("Fix the root cause at: {loc}. Suspected problem: {hyp}", None),
    ("Consider a different root cause than the most obvious one. Location to consider: {loc}. "
     "Suspected problem: {hyp}", 0.7),
    ("Fix it so that every input the issue implies is handled, including boundary cases "
     "(empty, zero, single element, None). Location: {loc}. Suspected problem: {hyp}", 0.7),
    ("Propose the most minimal possible correct change. Location: {loc}. Suspected problem: {hyp}", 0.9),
]


def _edits(data: dict | None) -> list[Edit]:
    out = []
    for e in (data or {}).get("edits") or []:
        if isinstance(e, dict) and e.get("path") and e.get("search") is not None:
            out.append(Edit(str(e["path"]).lstrip("./"), str(e["search"]), str(e.get("replace", ""))))
    return out


def generate(gateway, ledger: Ledger, repo_root: Path, prefix: str, locations: list[dict],
             start: int, k: int, parallel: bool = True) -> list[Candidate]:
    """Candidates c{start+1} .. c{start+k}."""
    jobs = []
    for j in range(k):
        n = start + j
        loc = locations[n % len(locations)] if locations else {"file": "?", "symbol": "?", "hypothesis": ""}
        framing, temp = FRAMINGS[n % len(FRAMINGS)]
        suffix = framing.format(loc=f"{loc['file']} :: {loc['symbol']}", hyp=loc.get("hypothesis", ""))
        jobs.append((f"c{n + 1}", suffix, temp))

    def one(job):
        cid, suffix, temp = job
        user = f"{prefix}\n\nTASK FOR THIS PROPOSAL:\n{suffix}"
        data = ask_json(gateway, "crux_candidate", user, ledger, "candidates", temperature=temp, required=("edits",))
        cand = build_candidate(repo_root, cid, str((data or {}).get("hypothesis", ""))[:200], _edits(data))
        if cand.status == "invalid" and data is not None:
            repair = (f"{user}\n\nYOUR PREVIOUS EDIT COULD NOT BE APPLIED: {cand.error}\n"
                      "Copy the search text exactly from the code shown (a few complete, unique lines).")
            data = ask_json(gateway, "crux_candidate", repair, ledger, "repair", required=("edits",))
            fixed = build_candidate(repo_root, cid, cand.hypothesis, _edits(data))
            if fixed.status != "invalid":
                fixed.notes.append(f"edit repaired after: {cand.error}")
                return fixed
            fixed.notes.append(f"first attempt: {cand.error}")
            return fixed
        return cand

    if parallel and len(jobs) > 1:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            return list(pool.map(one, jobs))
    return [one(j) for j in jobs]
