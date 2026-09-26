import shutil
from pathlib import Path

import pytest

from raven.verify.behavior_diff import (
    diff_function_outputs,
    diff_zero_arg_functions,
    discover_zero_arg_functions,
    find_collateral_changes,
)
from raven.verify.sbfl import run_sbfl
from raven.verify.trace import trace_test

TOY_REPO = Path(__file__).parent / "fixtures" / "toy_repo"


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(TOY_REPO, dest)
    return dest


# -- trace ---------------------------------------------------------------


def test_trace_reports_the_failing_assertion_and_call_chain(repo):
    story = trace_test(repo, "tests/test_arithmetic.py::test_average")
    assert "AssertionError" in story
    assert "average()" in story
    assert "test_average()" in story


def test_trace_reports_passed_outcome_for_a_passing_test(repo):
    story = trace_test(repo, "tests/test_arithmetic.py::test_add")
    assert "PASSED" in story


def test_trace_degrades_gracefully_on_bad_target_format(repo):
    story = trace_test(repo, "not-a-valid-target")
    assert "trace skipped" in story


def test_trace_degrades_gracefully_on_missing_file(repo):
    story = trace_test(repo, "tests/does_not_exist.py::test_x")
    assert story  # some explanatory string, not a crash


# -- sbfl ------------------------------------------------------------------


def test_sbfl_ranks_the_actual_buggy_line_highest(repo):
    targets = [
        "tests/test_arithmetic.py::test_average",
        "tests/test_arithmetic.py::test_add",
        "tests/test_arithmetic.py::test_subtract",
    ]
    ranked = run_sbfl(repo, targets)
    assert ranked
    top_location, top_score = ranked[0]
    assert "arithmetic.py:11" in top_location  # the off-by-one return line
    assert top_score == 1.0


def test_sbfl_returns_empty_when_no_targets():
    assert run_sbfl(Path("."), []) == []


def test_sbfl_returns_empty_when_all_tests_pass(repo):
    targets = ["tests/test_arithmetic.py::test_add", "tests/test_arithmetic.py::test_subtract"]
    assert run_sbfl(repo, targets) == []


# -- behavior diff -----------------------------------------------------------


PRE_SOURCE = "def average(numbers=(2, 4, 6)):\n    return sum(numbers) / (len(numbers) + 1)\n"
POST_SOURCE_FIXED = "def average(numbers=(2, 4, 6)):\n    return sum(numbers) / len(numbers)\n"
POST_SOURCE_COLLATERAL = (
    "def average(numbers=(2, 4, 6)):\n    return sum(numbers) / len(numbers)\n\n"
    "def bonus(x=1):\n    return x + 999\n"
)
PRE_SOURCE_WITH_BONUS = (
    "def average(numbers=(2, 4, 6)):\n    return sum(numbers) / (len(numbers) + 1)\n\n"
    "def bonus(x=1):\n    return x + 1\n"
)


def test_discover_zero_arg_functions_excludes_required_args():
    source = "def a():\n    pass\ndef b(x):\n    pass\ndef c(x=1):\n    pass\n"
    assert discover_zero_arg_functions(source) == ["a", "c"]


def test_diff_zero_arg_functions_detects_the_intended_change():
    changes = diff_zero_arg_functions(PRE_SOURCE, POST_SOURCE_FIXED)
    assert "average" in changes
    assert changes["average"][0]["pre"] == 3.0
    assert changes["average"][0]["post"] == 4.0


def test_diff_function_outputs_is_empty_when_nothing_changed():
    changes = diff_function_outputs(PRE_SOURCE, PRE_SOURCE, {"average": [((), None)]})
    assert changes == {}


def test_find_collateral_changes_excludes_the_target_function():
    changes = diff_zero_arg_functions(PRE_SOURCE_WITH_BONUS, POST_SOURCE_COLLATERAL)
    assert "average" in changes and "bonus" in changes
    collateral = find_collateral_changes(changes, target_function="average")
    assert collateral == ["bonus"]


def test_find_collateral_changes_with_no_target_returns_everything():
    changes = {"a": [{}], "b": [{}]}
    assert set(find_collateral_changes(changes, None)) == {"a", "b"}


def test_diff_function_outputs_degrades_on_syntax_error_source():
    changes = diff_function_outputs("def broken(:\n", PRE_SOURCE, {"average": [((), None)]})
    assert changes == {}
