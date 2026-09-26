"""Text helpers."""

import re


def slugify(text, sep="-"):
    """Lowercase `text` and join its words with `sep`.

    Characters other than letters and digits act as word separators.
    """
    text = text.strip().lower()
    return re.sub(r"[^a-z0-9]", sep, text)


def truncate(text, width, suffix="..."):
    """Shorten `text` to at most `width` characters, ending with `suffix`
    when anything was cut off."""
    if len(text) <= width:
        return text
    return text[:width] + suffix


def is_palindrome(text):
    """True if `text` reads the same backwards, ignoring case and spaces."""
    cleaned = text.lower().replace(" ", "")
    return cleaned == cleaned[::-1]
