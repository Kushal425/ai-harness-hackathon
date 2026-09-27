"""Probes: the executable unit Crux is built on.

A probe is setup code plus a list of expressions, each optionally carrying
what the issue says it SHOULD evaluate to (`expected`) or raise (`raises`).
The runner evaluates every expression in the target repository's own
interpreter and records exactly what happened (`=> repr` or `raises X`).
Observation and expectation are kept apart on purpose: what the code does
is measured, never guessed; only the expectation is the model's belief.

The same structure serves as the reproduction, the crux inputs, and each
candidate's behavioural fingerprint.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from raven.repo.interpreter import target_python
from raven.tools.shell import target_code_env

MARKER = "__RAVEN_PROBE__"

_RUNNER = r'''
import contextlib, io, json, signal, sys
spec = json.loads(sys.stdin.read())
root = spec["root"]
sys.path[:0] = [root, root + "/src"]

class _ProbeTimeout(BaseException):
    pass

def _alarm(*_):
    raise _ProbeTimeout()

def _repr(v):
    r = repr(v)
    return r if len(r) <= 300 else r[:297] + "..."

def _names(exc_type):
    return {c.__name__ for c in exc_type.__mro__}

g = {"__name__": "__raven_probe__"}
out = {"setup_error": None, "results": []}
try:
    with contextlib.redirect_stdout(io.StringIO()):
        exec(spec.get("setup") or "", g)
except BaseException as e:
    out["setup_error"] = f"{type(e).__name__}: {e}"[:300]
    print(MARKER + json.dumps(out)); sys.exit(0)

use_alarm = hasattr(signal, "SIGALRM")
if use_alarm:
    signal.signal(signal.SIGALRM, _alarm)
for case in spec["cases"]:
    ns = dict(g)
    res = {"outcome": None, "match": None, "detail": ""}
    value, raised = None, None
    try:
        if use_alarm:
            signal.alarm(3)
        with contextlib.redirect_stdout(io.StringIO()):
            if case.get("setup"):
                exec(case["setup"], ns)
            value = eval(compile(case["expr"], "<probe>", "eval"), ns)
        res["outcome"] = "=> " + _repr(value)
    except _ProbeTimeout:
        res["outcome"] = "timeout"
    except SyntaxError as e:
        raised = e
        res["outcome"] = "invalid probe: " + str(e)[:120]
    except BaseException as e:
        raised = e
        res["outcome"] = "raises " + type(e).__name__
        res["detail"] = str(e)[:200]
    finally:
        if use_alarm:
            signal.alarm(0)
    if case.get("raises"):
        res["match"] = raised is not None and case["raises"] in _names(type(raised))
    elif case.get("expected") is not None:
        if raised is not None:
            res["match"] = False
        else:
            try:
                expected = eval(case["expected"], ns)
                res["match"] = bool(value == expected)
            except BaseException:
                # not valid as Python (e.g. an unquoted string: hello): compare as text
                want = case["expected"].strip()
                res["match"] = _repr(value) == want or str(value) == want or str(value) == want.strip("'\"")
    out["results"].append(res)
print(MARKER + json.dumps(out))
'''.replace("MARKER", repr(MARKER))


@dataclass
class Case:
    expr: str
    expected: str | None = None   # Python expression for the value the issue says is right
    raises: str | None = None     # exception class name the issue says should be raised
    setup: str | None = None      # per-case setup (e.g. imports of a harvested test file)
    origin: str = "probe"         # probe | variant | harvested | model


@dataclass
class Probe:
    setup: str
    cases: list[Case] = field(default_factory=list)


@dataclass
class CaseResult:
    outcome: str | None
    match: bool | None
    detail: str = ""


@dataclass
class ProbeRun:
    setup_error: str | None
    results: list[CaseResult]

    @property
    def fingerprint(self) -> tuple:
        return tuple(r.outcome for r in self.results)

    @property
    def expectation_known(self) -> bool:
        return any(r.match is not None for r in self.results)

    @property
    def all_expected_met(self) -> bool:
        known = [r.match for r in self.results if r.match is not None]
        return bool(known) and all(known)

    @property
    def matched(self) -> int:
        return sum(1 for r in self.results if r.match)


def run_probe(repo_root: Path, probe: Probe, timeout: int = 60) -> ProbeRun:
    """Evaluate every case of `probe` against the repo's CURRENT files, in
    the target repo's interpreter. Never raises."""
    repo_root = Path(repo_root).resolve()
    spec = {"root": str(repo_root), "setup": probe.setup, "cases": [asdict(c) for c in probe.cases]}
    env = target_code_env(repo_root)
    env["PYTHONHASHSEED"] = "0"  # deterministic set/dict reprs -> stable fingerprints
    env["PYTHONPATH"] = str(repo_root / "src") + ":" + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            [target_python(repo_root), "-c", _RUNNER], input=json.dumps(spec), cwd=str(repo_root),
            env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ProbeRun("probe timed out", [CaseResult("timeout", None) for _ in probe.cases])
    except OSError as exc:
        return ProbeRun(f"could not run probe: {exc}", [CaseResult(None, None) for _ in probe.cases])
    line = next((l for l in reversed(proc.stdout.splitlines()) if l.startswith(MARKER)), None)
    if line is None:
        err = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["no output"]
        return ProbeRun(f"probe crashed: {err[0][:200]}", [CaseResult("crashed", None) for _ in probe.cases])
    data = json.loads(line[len(MARKER):])
    results = [CaseResult(**r) for r in data["results"]]
    return ProbeRun(data["setup_error"], results)


def describe(probe: Probe, run: ProbeRun) -> str:
    """Compact table for prompts/reports: expr | current outcome | expected."""
    rows = []
    for case, res in zip(probe.cases, run.results):
        want = f"raises {case.raises}" if case.raises else (f"=> {case.expected}" if case.expected else "?")
        mark = {True: "OK", False: "WRONG", None: ""}[res.match]
        rows.append(f"{case.expr}  ->  {res.outcome}   (expected {want}) {mark}".rstrip())
    if run.setup_error:
        rows.insert(0, f"setup failed: {run.setup_error}")
    return "\n".join(rows)
