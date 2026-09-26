"""Helpers for splitting sequences."""


def window(items, size):
    """Sliding windows of `size` consecutive items.

    Empty input yields no windows (an empty list), never a list holding an
    empty window.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[i:i + size] for i in range(len(items) - size + 1)]


def chunk(items, size):
    """Split `items` into consecutive chunks of `size` items."""
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[i:i + size] for i in range(0, len(items) - size + 1, size)]
