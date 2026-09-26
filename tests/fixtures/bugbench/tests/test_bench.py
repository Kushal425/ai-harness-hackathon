"""Visible tests: they pass on the buggy code, as real suites often do."""

import pytest

from bench.collections_ import chunk, dedupe, merge_intervals, window
from bench.numbers import clamp, mean, roman_to_int
from bench.text import is_palindrome, slugify, truncate
from bench.timeparse import parse_duration


def test_slugify_simple():
    assert slugify("Hello") == "hello"


def test_truncate_short_text_untouched():
    assert truncate("hi", 10) == "hi"


def test_palindrome():
    assert is_palindrome("Never odd or even")


def test_clamp_low():
    assert clamp(-5, 0, 10) == 0


def test_clamp_rejects_bad_range():
    with pytest.raises(ValueError):
        clamp(1, 5, 0)


def test_roman_additive():
    assert roman_to_int("XVI") == 16


def test_mean():
    assert mean([1, 2, 3]) == 2
    with pytest.raises(ValueError):
        mean([])


def test_dedupe_no_duplicates_kept():
    assert sorted(dedupe([3, 1, 3])) == [1, 3]


def test_merge_overlapping():
    assert merge_intervals([[1, 3], [2, 5]]) == [[1, 5]]


def test_chunk_even():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_window_empty():
    assert window([], 2) == []


def test_parse_duration_single_unit():
    assert parse_duration("90s") == 90
    assert parse_duration("5m") == 300
