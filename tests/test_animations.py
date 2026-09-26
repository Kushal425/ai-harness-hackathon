from raven.ui.animations import should_show_animation


def test_never_for_ui_mode_off():
    assert should_show_animation(is_tty=True, term="xterm-256color", ui_mode="off") is False


def test_never_for_non_tty_piped_or_autonomous():
    assert should_show_animation(is_tty=False, term="xterm-256color", ui_mode="auto") is False


def test_never_for_dumb_terminal():
    assert should_show_animation(is_tty=True, term="dumb", ui_mode="auto") is False


def test_shown_for_normal_interactive_tty():
    assert should_show_animation(is_tty=True, term="xterm-256color", ui_mode="auto") is True


def test_shown_when_ui_mode_explicitly_on_even_if_term_unset():
    assert should_show_animation(is_tty=True, term=None, ui_mode="on") is True
