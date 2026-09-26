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


def test_parses_bare_json_tool_call_without_fence():
    # gpt-oss-20b in the wild: the action with no ```action fence at all
    action = parse_action('{"tool":"done","args":{"summary":"A MERN app."}}')
    assert action.tool == "done" and action.args["summary"] == "A MERN app."


def test_parses_tool_call_in_json_fence_and_after_prose():
    assert parse_action('```json\n{"tool": "read", "args": {"path": "a.py"},}\n```').tool == "read"
    assert parse_action('Let me check.\n{"tool": "search", "args": {"pattern": "x {"}}').args == {"pattern": "x {"}


def test_plain_prose_and_non_tool_json_still_rejected():
    for text in ("Hello there.", 'config is {"a": 1}'):
        with pytest.raises(ActionParseError):
            parse_action(text)


def test_chat_never_shows_a_broken_tool_call_as_the_answer(tmp_path):
    from raven.core.executor import run_single_loop
    from raven.llm.fake import FakeClient
    from raven.llm.gateway import LLMGateway
    from raven.recovery.checkpoints import CheckpointManager
    from raven.tools.registry import RunContext, build_default_registry

    truncated = '{"tool":"done","args":{"summary":"cut off'
    fixed = '{"tool":"done","args":{"summary":"the real answer"}}'
    result = run_single_loop(
        LLMGateway(FakeClient([truncated, fixed])), build_default_registry(),
        RunContext(repo_root=tmp_path, mode="chat"), CheckpointManager(tmp_path),
        goal="what is this?", answer_mode=True,
    )
    assert result.summary == "the real answer"
