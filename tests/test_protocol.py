import pytest

from raven.core.protocol import ActionParseError, parse_action


def test_parses_simple_action():
    text = 'I will read the file.\n```action\n{"tool": "read", "args": {"path": "x.py"}}\n```'
    action = parse_action(text)
    assert action.tool == "read"
    assert action.args == {"path": "x.py"}


def test_parses_done_with_no_trailing_comma_repair():
    text = '```action\n{"tool": "done", "args": {"summary": "fixed it",}}\n```'
    action = parse_action(text)
    assert action.tool == "done"
    assert action.args["summary"] == "fixed it"


def test_uses_last_block_when_multiple_present():
    text = (
        '```action\n{"tool": "read", "args": {}}\n```\n'
        'actually...\n'
        '```action\n{"tool": "search", "args": {"pattern": "x"}}\n```'
    )
    action = parse_action(text)
    assert action.tool == "search"


def test_raises_on_missing_block():
    with pytest.raises(ActionParseError):
        parse_action("just some plain text, no action block")


def test_raises_on_malformed_json():
    with pytest.raises(ActionParseError):
        parse_action('```action\nnot json at all {{{\n```')


def test_defaults_args_to_empty_dict():
    action = parse_action('```action\n{"tool": "done"}\n```')
    assert action.args == {}
