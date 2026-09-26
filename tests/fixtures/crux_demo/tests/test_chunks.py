import pytest

from listkit.chunks import chunk, window


def test_window_basic():
    assert window([1, 2, 3], 2) == [[1, 2], [2, 3]]


def test_window_empty():
    assert window([], 2) == []


def test_chunk_even():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_chunk_rejects_zero():
    with pytest.raises(ValueError):
        chunk([1, 2], 0)
