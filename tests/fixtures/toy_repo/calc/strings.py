def reverse(text):
    return text[::-1]


def is_palindrome(text):
    # BUG: compares text to itself instead of its reverse
    cleaned = text.lower().replace(" ", "")
    return cleaned == cleaned
