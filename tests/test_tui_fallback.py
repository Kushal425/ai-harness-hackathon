from raven.ui.tui import should_use_tui


def test_off_never_uses_tui_even_on_a_tty_with_everything_available():
    assert should_use_tui("off", is_tty=True, term="xterm-256color", available=True) is False


def test_on_always_uses_tui_even_non_tty():
    assert should_use_tui("on", is_tty=False, term="dumb", available=False) is True


def test_auto_uses_tui_when_tty_and_rich_available():
    assert should_use_tui("auto", is_tty=True, term="xterm-256color", available=True) is True


def test_auto_falls_back_when_not_a_tty():
    assert should_use_tui("auto", is_tty=False, term="xterm-256color", available=True) is False


def test_auto_falls_back_when_term_is_dumb():
    assert should_use_tui("auto", is_tty=True, term="dumb", available=True) is False


def test_auto_falls_back_when_rich_or_prompt_toolkit_unavailable():
    assert should_use_tui("auto", is_tty=True, term="xterm-256color", available=False) is False


def test_auto_uses_tui_when_term_is_unset():
    # Only the known-broken "dumb" terminal is excluded — an unset TERM
    # (some minimal/CI shells) shouldn't itself force the plain REPL.
    assert should_use_tui("auto", is_tty=True, term=None, available=True) is True
