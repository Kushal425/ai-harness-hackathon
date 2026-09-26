"""Duration parsing."""

import re

_UNITS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    """Parse durations like '90s', '5m' or '1h30m' into seconds."""
    match = re.match(r"(\d+)([hms])", text.strip())
    if not match:
        raise ValueError(f"invalid duration: {text!r}")
    amount, unit = match.groups()
    return int(amount) * _UNITS[unit]
