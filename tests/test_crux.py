"""Crux end to end (FakeClient-scripted model, real execution).

The demo scenario: `chunk()` drops the last partial chunk. Three candidate
fixes all pass the visible tests AND the issue's own example; one of them
(c2) is plausible but wrong -- it returns [[]] for empty input. Crux must
find that single disagreeing input, ask one question about it, and pick a
correct fix, which a hidden test then confirms.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from raven.config import RunSettings
from raven.core.orchestrator import run_orchestrator
from raven.crux.pipeline import run_crux
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway

DEMO = Path(__file__).parent / "fixtures" / "crux_demo"
ISSUE = "chunk([1, 2, 3, 4, 5], 2) returns [[1, 2], [3, 4]] -- the last partial chunk [5] is dropped."
BUGGY = "    return [items[i:i + size] for i in range(0, len(items) - size + 1, size)]"


def j(obj) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


LOCALIZE = j({"locations": [{"file": "listkit/chunks.py", "symbol": "chunk",
                             "hypothesis": "the range stop len(items) - size + 1 skips the trailing partial chunk"}]})
PROBE = j({"setup": "from listkit.chunks import chunk", "cases": [
    {"expr": "chunk([1, 2, 3, 4, 5], 2)", "expected": "[[1, 2], [3, 4], [5]]", "raises": None},
    {"expr": "chunk([1, 2, 3], 3)", "expected": "[[1, 2, 3]]", "raises": None},
]})
CAND_A = j({"hypothesis": "stop the range at len(items)", "edits": [{
    "path": "listkit/chunks.py", "search": BUGGY,
    "replace": "    return [items[i:i + size] for i in range(0, len(items), size)]"}]})
CAND_B = j({"hypothesis": "compute the number of chunks with ceiling division", "edits": [{
    "path": "listkit/chunks.py", "search": BUGGY,
    "replace": "    n = max(1, -(-len(items) // size))\n    return [items[i * size:(i + 1) * size] for i in range(n)]"}]})
CAND_C = j({"hypothesis": "build the chunks in a loop up to the end", "edits": [{
    "path": "listkit/chunks.py", "search": BUGGY,
    "replace": "    out = []\n    for i in range(0, len(items), size):\n        out.append(items[i:i + size])\n    return out"}]})
VERDICT = j({"choice": "A", "because": "window() documents that empty input yields no windows and "
                                       "tests/test_chunks.py asserts window([], 2) == []"})
HIDDEN_TEST = (
    "from listkit.chunks import chunk\n\n"
    "def test_hidden():\n"
    "    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]\n"
    "    assert chunk([], 2) == []\n"
)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    _git(dest, "init", "-q")
    _git(dest, "add", "-A")
    subprocess.run(["git", "-C", str(dest), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"])
    return dest


def _settings(**kw):
    base = dict(candidates=3, max_candidates=4, parallel_calls=False, trace=False, lessons=False)
    base.update(kw)
    return RunSettings(**base)


def test_crux_rejects_the_plausible_wrong_patch(repo):
    client = FakeClient([LOCALIZE, PROBE, CAND_A, CAND_B, CAND_C, VERDICT])
    events = []
    result = run_orchestrator(LLMGateway(client), repo, ISSUE, strategy="crux", settings=_settings(),
                              on_event=lambda e, d: events.append((e, d)))

    assert result.accepted and result.verified, result.reason
    crux = result.evidence["crux"]
    # all three candidates survive the visible tests and the issue's example ...
    assert [c["status"] for c in crux["candidates"]] == ["alive", "rejected-by-crux", "alive"]
    # ... but split into two behaviour clusters, separated by ONE question
    assert crux["clusters"] == [["c1", "c3"], ["c2"]]
    [verdict] = crux["verdicts"]
    assert verdict["input"] == "chunk([], 2)"
    assert {o["outcome"] for o in verdict["options"].values()} == {"=> []", "=> [[]]"}
    assert any("window([], 2) == []" in e for e in verdict["evidence"])
    assert crux["winner"] == "c1"  # the smaller of the two correct fixes
    assert result.evidence["evidence_score"] == 1.0
    assert len(client.calls) == 6  # localize, probe, 3 candidates, 1 adjudication

    # the working tree holds only the fix -- and the hidden grader test passes
    assert _git(repo, "status", "--porcelain").splitlines() == [" M listkit/chunks.py"]
    (repo / "test_hidden.py").write_text(HIDDEN_TEST)
    hidden = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_hidden.py"],
                            cwd=repo, capture_output=True, text=True)
    assert "1 passed" in hidden.stdout, hidden.stdout + hidden.stderr
    assert "Crux ledger" in result.report_path.read_text()
    assert any(e == "crux" and d["stage"] == "verdict" for e, d in events)


def test_without_crux_the_wrong_patch_would_have_passed_everything_visible(repo):
    """The point of the demo: c2 passes the visible tests and the issue's
    example -- only the crux (or the hidden test) exposes it."""
    from raven.crux.patching import Edit, applied, build_candidate
    from raven.crux.probe import Case, Probe, run_probe
    from raven.crux.regression import run_tests

    b = json.loads(CAND_B.split("\n", 1)[1].rsplit("```", 1)[0])["edits"][0]
    cand = build_candidate(repo, "c2", "", [Edit(**b)])
    with applied(repo, cand):
        ran, failed, _ = run_tests(repo, ["tests/test_chunks.py"])
        issue_example = run_probe(repo, Probe("from listkit.chunks import chunk",
                                              [Case("chunk([1, 2, 3, 4, 5], 2)", expected="[[1, 2], [3, 4], [5]]"),
                                               Case("chunk([], 2)", expected="[]")]))
    assert ran and failed == []
    assert issue_example.results[0].match is True     # fixes the reported example
    assert issue_example.results[1].match is False    # ...but chunk([], 2) == [[]]


def test_edit_that_does_not_apply_gets_one_repair_call(repo):
    bad = j({"hypothesis": "x", "edits": [{"path": "listkit/chunks.py", "search": "return nonsense", "replace": "x"}]})
    client = FakeClient([LOCALIZE, PROBE, bad, CAND_A])
    out = run_crux(LLMGateway(client), repo, ISSUE, k=1, max_candidates=1, parallel=False)
    assert out.winner is not None and out.winner.id == "c1"
    assert any("repaired" in n for n in out.winner.notes)
    assert out.ledger.stages["repair"].calls == 1


def test_wrong_location_is_diagnosed_and_fed_back(repo):
    # round 1: a candidate that edits window() -- behaviour of chunk unchanged
    wrong = j({"hypothesis": "window is wrong", "edits": [{
        "path": "listkit/chunks.py", "search": "    return [items[i:i + size] for i in range(len(items) - size + 1)]",
        "replace": "    return [items[i:i + size] for i in range(max(0, len(items) - size + 1))]"}]})
    client = FakeClient([LOCALIZE, PROBE, wrong, CAND_A, CAND_C])
    out = run_crux(LLMGateway(client), repo, ISSUE, k=1, max_candidates=3, parallel=False)
    assert out.candidates[0].status == "misses-issue"
    assert any("did not change at all" in n for n in out.notes)
    # the feedback reached the next round's prompt
    round2_prompt = client.calls[3][1].content
    assert "WHAT FAILED IN EARLIER ATTEMPTS" in round2_prompt and "cause is probably elsewhere" in round2_prompt
    assert out.winner is not None


def test_non_python_repo_falls_back_to_the_agent_loop(tmp_path):
    (tmp_path / "app.js").write_text("function f(){ return 1 }\n")
    done = '```action\n{"tool": "done", "args": {"summary": "nothing to do"}}\n```'
    result = run_orchestrator(LLMGateway(FakeClient([done])), tmp_path, "explain what f does?",
                              strategy="crux", settings=_settings())
    assert result.evidence is None or result.evidence.get("strategy") != "crux"
