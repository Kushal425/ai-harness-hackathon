"""Crux building blocks: issue card, repo map, probes, patching, disagreement."""

from pathlib import Path

from raven.crux.disagree import best_crux, cluster, harvested_cases, variant_cases
from raven.crux.issue import build_issue_card
from raven.crux.llm import extract_json
from raven.crux.patching import Edit, apply_edit, build_candidate
from raven.crux.probe import Case, Probe, run_probe
from raven.crux.repomap import RepoMap, module_name

TOY = Path(__file__).parent / "fixtures" / "toy_repo"


def test_issue_card_extracts_identifiers_frames_and_exceptions():
    card = build_issue_card(
        "Calling `parse_duration('1h')` fails:\n"
        'Traceback (most recent call last):\n  File "timeutil/parse.py", line 12, in parse_duration\n'
        "ValueError: bad unit\nSee timeutil/parse.py"
    )
    assert card.identifiers[0] == "parse_duration"
    assert card.frames == [("timeutil/parse.py", 12, "parse_duration")]
    assert "ValueError" in card.exceptions and "timeutil/parse.py" in card.paths


def test_repo_map_ranks_the_named_function_first_and_knows_import_paths():
    rmap = RepoMap(TOY)
    ranked = rmap.rank(build_issue_card("average() of [2,4,6] is wrong"))
    assert ranked[0].qualname == "average" and ranked[0].module == "calc.arithmetic"
    assert "tests/test_arithmetic.py" in rmap.test_files
    assert module_name("src/pkg/mod.py") == "pkg.mod" and module_name("pkg/__init__.py") == "pkg"


def test_probe_records_values_exceptions_and_expectations_separately():
    run = run_probe(TOY, Probe("from calc.arithmetic import average", [
        Case("average([2, 4, 6])", expected="4"),
        Case("average([])", raises="ZeroDivisionError"),
        Case("int('x')", raises="ValueError"),
    ]))
    assert [r.outcome for r in run.results] == ["=> 3.0", "=> 0.0", "raises ValueError"]
    assert [r.match for r in run.results] == [False, False, True]
    assert run.setup_error is None and not run.all_expected_met


def test_probe_timeout_and_bad_setup_never_raise():
    assert run_probe(TOY, Probe("import nope_not_a_module", [Case("1")])).setup_error.startswith("ModuleNotFoundError")


def test_apply_edit_exact_normalized_and_ambiguous():
    text = "def f(x):\n    return x+1\n\ndef g(x):\n    return x+1\n"
    assert apply_edit(text, Edit("a.py", "def f(x):\n    return x+1", "def f(x):\n    return x+2"))[0].count("x+2") == 1
    assert "2 places" in apply_edit(text, Edit("a.py", "    return x+1", "    return 0"))[1]
    assert apply_edit("a  =  1\n", Edit("a.py", "a = 1", "a = 2"))[0] == "a = 2\n"  # whitespace-normalised


def test_build_candidate_rejects_syntax_errors_and_no_ops(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n")
    assert build_candidate(tmp_path, "c1", "", [Edit("m.py", "x = 1", "x = (")]).status == "invalid"
    assert build_candidate(tmp_path, "c2", "", [Edit("m.py", "x = 1", "x = 1")]).error == "edits change nothing"
    assert build_candidate(tmp_path, "c3", "", [Edit("../m.py", "x", "y")]).status == "invalid"
    ok = build_candidate(tmp_path, "c4", "", [Edit("m.py", "x = 1", "x = 2")])
    assert ok.status == "built" and (tmp_path / "m.py").read_text() == "x = 1\n"  # nothing written yet


def test_variants_harvest_and_crux_choice():
    variants = [c.expr for c in variant_cases(Probe("", [Case("chunk([1, 2, 3], 2)")]))]
    assert "chunk([], 2)" in variants and "chunk([1, 2, 3], 0)" in variants
    harvested = harvested_cases(RepoMap(TOY), {"average"})
    assert harvested and harvested[0].setup.startswith("from calc.arithmetic import")
    clusters = cluster({"a": ("1", "x"), "b": ("1", "y"), "c": ("1", "x")})
    assert [c.members for c in clusters] == [["a", "c"], ["b"]]
    assert best_crux(clusters, [Case("e0"), Case("e1")]) == 1


def test_extract_json_handles_fences_bare_and_trailing_commas():
    assert extract_json('```json\n{"a": 1,}\n```') == {"a": 1}
    assert extract_json('Sure! {"choice": "B", "because": "x"} done') == {"choice": "B", "because": "x"}
    assert extract_json("no json here") is None


def test_natural_language_expectations_are_dropped_not_fatal():
    from raven.crux.pipeline import _valid_expr, _valid_name

    assert _valid_expr("[[1, 2], [5]]") == "[[1, 2], [5]]"
    assert _valid_expr("should return 4") is None and _valid_expr(None) is None
    assert _valid_name("builtins.ValueError") == "ValueError" and _valid_name("an error") is None


def test_timeouts_never_split_clusters():
    # c3 timed out on input 1 (machine load) but behaves like c1 everywhere else
    clusters = cluster({"c1": ("=> 1", "=> []"), "c3": ("=> 1", "timeout"), "c2": ("=> 1", "=> [[]]")})
    assert [sorted(c.members) for c in clusters] == [["c1", "c2", "c3"]]  # input 1 masked for everyone
    clusters = cluster({"c1": ("=> []", "=> 2"), "c3": ("=> []", "timeout"), "c2": ("=> [[]]", "=> 2")})
    assert sorted(c.members for c in clusters) == [["c1", "c3"], ["c2"]]


# -- regressions from the live Qwen2.5-Coder-7B benchmark ----------------------

def test_unquoted_string_expectation_still_matches(tmp_path):
    # the model wrote  "expected": "hello"  (meaning the string) -- a NameError as Python
    run = run_probe(tmp_path, Probe("", [Case("'hel' + 'lo'", expected="hello"), Case("'x'", expected="hello")]))
    assert [r.match for r in run.results] == [True, False]


def test_expectation_copied_from_the_reported_bug_is_dropped(tmp_path):
    from raven.crux.pipeline import _drop_contradictions

    (tmp_path / "m.py").write_text("def f(xs):\n    return xs[:2]\n")
    issue = "f([1, 2, 3]) returns [1, 2] -- the last item is dropped."
    probe = Probe("from m import f", [Case("f([1, 2, 3])", expected="[1, 2]"),       # the bug, copied as 'expected'
                                      Case("f([9])", expected="[9]")])               # fine: not quoted in the issue
    probe, run = _drop_contradictions(probe, run_probe(tmp_path, probe), issue, tmp_path)
    assert probe.cases[0].expected is None and probe.cases[1].expected == "[9]"


def test_variants_include_case_changes():
    exprs = [c.expr for c in variant_cases(Probe("", [Case("roman_to_int('IV')")]))]
    assert "roman_to_int('iv')" in exprs


def test_a_fix_that_crashes_where_the_original_did_not_is_rejected(tmp_path):
    """Live failure: the only surviving roman fix crashed on lowercase input
    (KeyError), was reported RESOLVED, and failed the hidden test."""
    import json
    import subprocess

    from raven.crux.pipeline import run_crux
    from raven.llm.fake import FakeClient
    from raven.llm.gateway import LLMGateway

    (tmp_path / "rn.py").write_text(
        'def roman_to_int(numeral):\n'
        '    values = {"I": 1, "V": 5, "X": 10}\n'
        '    return sum(values[ch] for ch in numeral.upper())\n')
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True)
    j = lambda o: "```json\n" + json.dumps(o) + "\n```"  # noqa: E731
    search = "    return sum(values[ch] for ch in numeral.upper())"
    crashy = ("    return sum(values[ch] if i == len(numeral) - 1 or values[ch] >= values[numeral[i + 1]] "
              "else -values[ch] for i, ch in enumerate(numeral.upper()))")
    good = ("    s = numeral.upper()\n    return sum(values[c] if i == len(s) - 1 or values[c] >= values[s[i + 1]] "
            "else -values[c] for i, c in enumerate(s))")
    script = [
        j({"locations": [{"file": "rn.py", "symbol": "roman_to_int", "hypothesis": "no subtractive notation"}]}),
        j({"setup": "from rn import roman_to_int", "cases": [{"expr": "roman_to_int('IV')", "expected": "4"}]}),
        j({"hypothesis": "subtract smaller before larger", "edits": [{"path": "rn.py", "search": search, "replace": crashy}]}),
        j({"hypothesis": "subtract smaller before larger, on the uppercased string",
           "edits": [{"path": "rn.py", "search": search, "replace": good}]}),
    ]
    out = run_crux(LLMGateway(FakeClient(script)), tmp_path, "roman_to_int('IV') returns 6, should be 4",
                   k=1, max_candidates=2, parallel=False)
    assert out.candidates[0].status == "new-crash" and "KeyError" in out.candidates[0].notes[0]
    assert out.winner is not None and out.winner.id == "c2"
