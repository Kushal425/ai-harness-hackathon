from raven.context.decay import HistoryEntry, shrink
from raven.context.engine import ContextEngine, ContextState


def test_shrink_keeps_recent_entries_intact():
    entry = HistoryEntry(turn=5, role="result", text="line1\nline2")
    assert shrink(entry, current_turn=6, half_life=3) == "line1\nline2"


def test_shrink_stubs_old_entries():
    entry = HistoryEntry(turn=1, role="result", text="a\nb\nc", path="x.py")
    stubbed = shrink(entry, current_turn=10, half_life=3)
    assert "omitted" in stubbed
    assert "x.py" in stubbed


def test_engine_assembles_system_and_user_messages():
    engine = ContextEngine(tool_docs="- read(path): reads a file")
    state = ContextState(task_card="fix the bug", digest_summary="python repo", plan_text="")
    messages = engine.assemble(state, current_turn=1)
    assert messages[0].role == "system"
    assert "read(path)" in messages[0].content
    assert messages[1].role == "user"
    assert "fix the bug" in messages[1].content
    assert "python repo" in messages[1].content


def test_engine_includes_decayed_history():
    engine = ContextEngine(tool_docs="", half_life=1)
    history = [HistoryEntry(turn=1, role="result", text="old detail", path="a.py")]
    state = ContextState(task_card="goal", history=history)
    messages = engine.assemble(state, current_turn=5)
    assert "omitted" in messages[1].content
