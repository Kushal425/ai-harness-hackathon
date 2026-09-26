import shutil
from pathlib import Path

import pytest

from raven.config import load_config
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway
from raven.session.manager import SessionManager, looks_like_an_issue

TOY_REPO = Path(__file__).parent / "fixtures" / "toy_repo"


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(TOY_REPO, dest)
    return dest


def _session(repo, responses):
    config = load_config()
    gateway = LLMGateway(FakeClient(scripted_responses=list(responses)))
    return SessionManager(config, gateway, repo)


UNDERSTAND_RESP = (
    '```understanding\n{"task_type": "bug_fix", "summary": "fix average()", '
    '"entities": {}, "acceptance_criteria": ["tests pass"], '
    '"verification_strategy": "existing_tests", "ambiguities": [], "risk": "low"}\n```'
)
PLAN_RESP = (
    '```plan\n{"steps": [{"action": "fix average()", "tools": ["edit", "tests"], '
    '"check": "tests pass"}]}\n```'
)
FIX_SEQUENCE = [
    '```action\n{"tool": "search", "args": {"pattern": "def average"}}\n```',
    '```action\n{"tool": "read", "args": {"path": "calc/arithmetic.py"}}\n```',
    '```action\n{"tool": "edit", "args": {"path": "calc/arithmetic.py", '
    '"search": "return sum(numbers) / (len(numbers) + 1)", '
    '"replace": "return sum(numbers) / len(numbers)"}}\n```',
    '```action\n{"tool": "tests", "args": {"target": "tests/test_arithmetic.py"}}\n```',
    '```action\n{"tool": "done", "args": {"summary": "fixed off-by-one bug in average()"}}\n```',
]


def test_unknown_slash_command_reports_itself():
    session = _session(Path("."), [])
    reply = session.handle_input("/nope")
    assert "unknown command" in reply
    assert "/nope" in reply


def test_help_lists_known_commands():
    session = _session(Path("."), [])
    reply = session.handle_input("/help")
    assert "/plan" in reply and "/undo" in reply


def test_plan_then_act_flow(repo):
    session = _session(repo, [UNDERSTAND_RESP, PLAN_RESP] + FIX_SEQUENCE)

    plan_reply = session.handle_input("/plan fix the average() bug")
    assert "plan mode" in plan_reply
    assert session.state.mode == "plan"
    assert session.state.plan is not None

    act_reply = session.handle_input("/act")
    assert "RESOLVED" in act_reply
    assert session.state.last_result.accepted


def test_act_executes_the_plan_already_shown_by_plan_not_a_recomputed_one(repo):
    """Regression test: /act used to silently call run_orchestrator with no
    understanding/plan, which recomputed both from scratch — discarding
    whatever /plan showed the user (and any edits they made to it). Assert
    by identity that /act's result carries the *exact same* Understanding
    and Plan objects /plan already produced, not new ones. Also assert the
    LLM call count matches exactly 2 (understand+plan) + len(FIX_SEQUENCE) +
    1 (end-of-run cross-task lesson extraction, plan §12.2) — any hidden
    recomputation would consume extra calls and desync the scripted
    sequence, breaking the fix and/or this count."""
    session = _session(repo, [UNDERSTAND_RESP, PLAN_RESP] + FIX_SEQUENCE + ['```lesson\n{"trigger": ""}\n```'])

    session.handle_input("/plan fix the average() bug")
    planned_understanding = session.state.understanding
    planned_plan = session.state.plan

    session.handle_input("/act")

    assert session.state.last_result.understanding is planned_understanding
    assert session.state.last_result.plan is planned_plan
    assert session.gateway.stats.calls == 2 + len(FIX_SEQUENCE) + 1

    fixed_content = (repo / "calc" / "arithmetic.py").read_text()
    assert "sum(numbers) / len(numbers)\n" in fixed_content
    assert session.state.last_result.evidence["repro_fixed"] is True


def test_act_without_a_plan_says_so():
    session = _session(Path("."), [])
    reply = session.handle_input("/act")
    assert "no plan yet" in reply


def test_undo_reverts_checkpoints_from_last_run(repo):
    session = _session(repo, [UNDERSTAND_RESP, PLAN_RESP] + FIX_SEQUENCE)
    session.handle_input("/plan fix the average() bug")
    session.handle_input("/act")

    fixed_content = (repo / "calc" / "arithmetic.py").read_text()
    assert "sum(numbers) / len(numbers)\n" in fixed_content

    undo_reply = session.handle_input("/undo")
    assert "reverted" in undo_reply
    restored_content = (repo / "calc" / "arithmetic.py").read_text()
    assert "sum(numbers) / (len(numbers) + 1)" in restored_content


def test_looks_like_an_issue_detects_stack_trace():
    text = 'Traceback (most recent call last):\n  File "x.py", line 1\nValueError: boom'
    assert looks_like_an_issue(text)


def test_looks_like_an_issue_detects_long_text():
    assert looks_like_an_issue("x " * 100)


def test_looks_like_an_issue_false_for_short_chat():
    assert not looks_like_an_issue("what does this repo do?")


def test_chat_mode_auto_detects_issue_and_runs_autonomous(repo):
    long_issue = (
        "average([2,4,6]) returns 3.0 instead of 4. Steps to reproduce: call "
        "average([2,4,6]). Expected: 4. Actual: 3.0. Looks like an off-by-one "
        "bug in calc/arithmetic.py's average() function that divides by the "
        "wrong count."
    )
    greeting = '```action\n{"tool": "done", "args": {"summary": "Hello!"}}\n```'
    lesson = "(no lesson)"  # the run's end-of-run lesson-extraction call
    session = _session(repo, FIX_SEQUENCE + [lesson, greeting])
    reply = session.handle_input(long_issue)
    assert "switching to autonomous mode" in reply
    assert session.state.last_result is not None and session.state.last_result.accepted
    # the run is one-shot: a following "hi" is chat, not a new coding task
    assert session.state.mode == "chat"
    run_before = session.state.last_result.run_id
    assert session.handle_input("hi") == "Hello!"
    assert session.state.last_result.run_id == run_before


def test_chat_mode_read_only_tool_query(repo):
    responses = [
        '```action\n{"tool": "search", "args": {"pattern": "def average"}}\n```',
        '```action\n{"tool": "done", "args": {"summary": "average() is defined in calc/arithmetic.py"}}\n```',
    ]
    session = _session(repo, responses)
    reply = session.handle_input("where is average() defined?")
    assert "average" in reply.lower()


def test_chat_reply_uses_full_answer_not_a_terse_recap(repo):
    """Regression test: chat mode used to return the terse done(summary=...)
    field verbatim, e.g. "Provided overview of capabilities." instead of a
    real answer. answer_mode now tells the model the summary IS the reply,
    and this test scripts a model that (correctly, per that instruction)
    puts the full answer in summary — proving the plumbing actually
    surfaces it to the user unmodified."""
    full_answer = (
        "Raven is a coding-agent harness: I can read/search/edit this repo, "
        "run its tests, and fix bugs when you ask. In chat mode I only use "
        "read-only tools; use /plan or /auto to make changes."
    )
    responses = [f'```action\n{{"tool": "done", "args": {{"summary": "{full_answer}"}}}}\n```']
    session = _session(repo, responses)
    reply = session.handle_input("what can you do?")
    assert reply == full_answer


def test_chat_reply_falls_back_to_models_own_words_not_a_blind_second_call(repo):
    """Regression test: when the model answers in plain prose without an
    action block (e.g. "hello" or "can you access my repo?" -- casual
    questions the model doesn't think need a tool), the old code discarded
    that reply and fired a second, context-free gateway.complete() with no
    system prompt mentioning tools at all -- producing confident-sounding
    but false claims like "I don't have the ability to access external
    repositories." The fix surfaces the model's own (tool-aware-context)
    reply instead of a second blind call.

    In chat (answer mode) a plain-prose reply with no action block is taken
    as the answer on the spot -- no format-reminder re-asks, and no blind
    fallback call past the scripted responses."""
    prose_reply = "Yes, in chat mode I can read and search this repository using read-only tools."
    session = _session(repo, [prose_reply] * 3)
    reply = session.handle_input("can you access my repo?")
    assert reply == prose_reply
    assert session.gateway.stats.calls == 1


def test_memory_command_shows_digest_summary(repo):
    session = _session(repo, [])
    reply = session.handle_input("/memory")
    assert "python" in reply.lower() or "language" in reply.lower()


def test_budget_command_reports_usage(repo):
    session = _session(repo, [])
    reply = session.handle_input("/budget")
    assert "calls" in reply and "tokens" in reply


def test_model_and_config_commands(repo):
    session = _session(repo, [])
    assert session.handle_input("/model") == session.config.llm.model
    assert "model:" in session.handle_input("/config")


def test_repo_command_switches_and_validates(repo, tmp_path):
    session = _session(repo, [])
    missing = session.handle_input(f"/repo {tmp_path / 'does-not-exist'}")
    assert "no such directory" in missing

    other = tmp_path / "other"
    other.mkdir()
    switched = session.handle_input(f"/repo {other}")
    assert "switched repo" in switched
    assert session.state.repo_root == other.resolve()


def test_clear_resets_history(repo):
    session = _session(repo, [])
    session.history.append(session.history[0])
    reply = session.handle_input("/clear")
    assert "cleared" in reply
    assert len(session.history) == 1


def test_repo_command_clones_a_git_url(tmp_path, monkeypatch):
    import raven.session.manager as manager_mod

    cloned = tmp_path / "cloned"
    cloned.mkdir()
    monkeypatch.setattr(manager_mod, "clone_repo", lambda url: cloned)
    session = _session(Path("."), [])
    assert "switched repo" in session.handle_input("/repo https://github.com/acme/calc.git")
    assert session.state.repo_root == cloned


def test_plain_repl_reads_a_multiline_paste_as_one_message(monkeypatch):
    import os
    import pty
    import sys

    from raven.ui.repl import read_message

    master, slave = pty.openpty()
    stdin = os.fdopen(slave, "r")
    monkeypatch.setattr(sys, "stdin", stdin)
    os.write(master, b"Title: average() is wrong\nSteps to reproduce:\naverage([2,4,6])\n")
    message = read_message("> ")
    assert message.splitlines() == ["Title: average() is wrong", "Steps to reproduce:", "average([2,4,6])"]
