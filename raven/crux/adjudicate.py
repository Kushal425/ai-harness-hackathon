"""Adjudication: the only place Crux asks the model whether something is
RIGHT -- and it asks about one concrete input with concrete competing
outputs (multiple choice), backed by evidence retrieved deterministically
from the repository. Weak models discriminate far better than they
generate, so the oracle is spent where it is cheapest and most reliable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from raven.crux.disagree import Cluster
from raven.crux.llm import Ledger, ask_json
from raven.crux.probe import Case
from raven.crux.repomap import FunctionInfo, RepoMap

LETTERS = "ABCDEFGH"


@dataclass
class Verdict:
    expr: str
    options: dict[str, dict]            # letter -> {"outcome", "members"}
    original: str | None
    choice: str                          # letter, or "U" (unspecified)
    because: str = ""
    evidence: list[str] = field(default_factory=list)

    @property
    def unspecified(self) -> bool:
        return self.choice not in self.options

    def keeps(self, cluster: Cluster, index: int) -> bool:
        return self.unspecified or cluster.fingerprint[index] == self.options[self.choice]["outcome"]


def _shape_patterns(expr: str) -> list[str]:
    pats = []
    if re.search(r"\[\s*\]|\(\s*\)|\{\s*\}|''|\"\"", expr):
        pats += ["[]", "()", "{}", "''", '""', "empty"]
    if "None" in expr:
        pats.append("None")
    if re.search(r"[(,]\s*0\s*[),]", expr):
        pats += ["0)", "zero"]
    if re.search(r"-\d", expr):
        pats += ["-1", "negative"]
    return pats


def gather_evidence(rmap: RepoMap, targets: list[FunctionInfo], case: Case, limit: int = 10) -> list[str]:
    names = {f.name for f in targets}
    out: list[str] = []
    for fn in targets:
        doc = ""
        src = rmap.sources.get(fn.path, "").splitlines()[fn.start - 1: fn.end]
        text = "\n".join(src)
        m = re.search(r'("""|\'\'\')(.*?)\1', text, re.DOTALL)
        if m:
            doc = " ".join(m.group(2).split())[:300]
        if doc:
            out.append(f"{fn.path}:{fn.start} docstring of {fn.name}: {doc}")
    shapes = _shape_patterns(case.expr)
    modules = {f.path for f in targets}
    scan = list(rmap.test_files) + sorted(modules)
    for path in scan:
        for lineno, line in enumerate(rmap.sources.get(path, "").splitlines(), start=1):
            s = line.strip()
            if not s or s.startswith("#") or len(s) > 160:
                continue
            about_target = any(re.search(rf"\b{re.escape(n)}\b", s) for n in names)
            same_shape = shapes and any(p in s for p in shapes) and ("assert" in s or "return" in s or "raise" in s)
            if (about_target and ("assert" in s or "raise" in s)) or same_shape:
                entry = f"{path}:{lineno}  {s}"
                if entry not in out:
                    out.append(entry)
            if len(out) >= limit:
                return out
    for doc in ("README.md", "README.rst", "docs/index.md"):
        for lineno, line in enumerate(_read(rmap.repo_root / doc), start=1):
            if any(re.search(rf"\b{re.escape(n)}\b", line) for n in names) and len(out) < limit:
                out.append(f"{doc}:{lineno}  {line.strip()[:160]}")
    return out


def _read(path: Path) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()[:400]
    except OSError:
        return []


def adjudicate(gateway, ledger: Ledger, issue: str, code: str, case: Case, index: int,
               clusters: list[Cluster], original_outcome: str | None, evidence: list[str]) -> Verdict:
    options: dict[str, dict] = {}
    for c in clusters:
        outcome = c.fingerprint[index]
        letter = next((l for l, o in options.items() if o["outcome"] == outcome), None)
        if letter is None:
            letter = LETTERS[len(options)]
            options[letter] = {"outcome": outcome, "members": []}
        options[letter]["members"].extend(c.members)
    lines = [f"{l}) {o['outcome']}    [candidate fix {', '.join(o['members'])}]" for l, o in options.items()]
    lines.append("U) The issue and repository do not determine this; any of the above is acceptable.")
    user = (
        f"ISSUE:\n{issue.strip()[:3000]}\n\n"
        f"CODE BEING FIXED (original):\n{code[:4000]}\n\n"
        f"QUESTION: after the fix, what should this do?\n    {case.expr}\n"
        f"(the ORIGINAL code gives: {original_outcome})\n\n" + "\n".join(lines) + "\n\n"
        "REPOSITORY EVIDENCE:\n" + ("\n".join(f"- {e}" for e in evidence) or "- (none found)") + "\n\n"
        'Answer with JSON: {"choice": "<letter>", "because": "<one sentence citing the issue or evidence>"}'
    )
    data = ask_json(gateway, "crux_adjudicate", user, ledger, "adjudicate", required=("choice",)) or {}
    choice = str(data.get("choice", "U")).strip().upper()[:1] or "U"
    return Verdict(expr=case.expr, options=options, original=original_outcome, choice=choice,
                   because=str(data.get("because", ""))[:300], evidence=evidence)
