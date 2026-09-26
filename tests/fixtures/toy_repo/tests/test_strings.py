from calc.strings import is_palindrome, reverse


def test_reverse():
    assert reverse("abc") == "cba"


def test_is_palindrome_true():
    assert is_palindrome("racecar") is True


def test_is_palindrome_false():
    assert is_palindrome("hello") is False
