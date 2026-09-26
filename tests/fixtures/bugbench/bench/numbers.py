"""Numeric helpers."""


def clamp(value, low, high):
    """Limit `value` to the inclusive range [low, high]."""
    if low > high:
        raise ValueError("low must not exceed high")
    return max(low, min(value, low))


def roman_to_int(numeral):
    """Convert a Roman numeral such as 'XIV' to an integer."""
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    return sum(values[ch] for ch in numeral.upper())


def mean(values):
    """Arithmetic mean of `values`. Raises ValueError for an empty input."""
    if not values:
        raise ValueError("mean of empty sequence")
    return sum(values) / len(values)
