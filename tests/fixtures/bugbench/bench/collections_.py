"""Sequence helpers."""


def dedupe(items):
    """Remove duplicates from `items`, keeping the first occurrence of each."""
    return list(set(items))


def merge_intervals(intervals):
    """Merge overlapping [start, end] intervals. Intervals that touch
    (one ends where the next starts) are merged too."""
    result = []
    for start, end in sorted(intervals):
        if result and start < result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def chunk(items, size):
    """Split `items` into consecutive chunks of `size` items."""
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[i:i + size] for i in range(0, len(items) - size + 1, size)]


def window(items, size):
    """Sliding windows of `size` items. Empty input yields no windows."""
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[i:i + size] for i in range(len(items) - size + 1)]
