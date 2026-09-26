import io

from rich.console import Console

from raven.ui.render import render_result_block, render_tool_line_text, render_verification


def _capture(renderable) -> str:
    buf = io.StringIO()
    Console(file=buf, width=100, no_color=True).print(renderable)
    return buf.getvalue()


def test_render_tool_line_text_running():
    line = render_tool_line_text("read", {"path": "src/auth/session.ts"}, ok=None)
    assert line == "├─ read       src/auth/session.ts"


def test_render_tool_line_text_success():
    line = render_tool_line_text("tests", {"target": "tests/session.test.py"}, ok=True)
    assert line.startswith("✓")
    assert "tests" in line
    assert "session.test.py" in line


def test_render_tool_line_text_failure():
    line = render_tool_line_text("tests", {}, ok=False)
    assert line.startswith("✗")


def test_render_tool_line_never_dumps_huge_payload():
    huge = {"path": "x" * 500}
    line = render_tool_line_text("read", huge, ok=True)
    assert len(line) < 120


def test_render_verification_shows_checks_and_score():
    evidence = {"repro_fixed": True, "no_new_failures": True, "evidence_score": 0.94}
    text = _capture(render_verification(evidence))
    assert "Original issue reproduced" in text
    assert "0.94" in text
    assert "%" not in text  # not a percent/gamified score


def test_render_verification_handles_none():
    text = _capture(render_verification(None))
    assert "no verification evidence" in text


def test_render_result_block_contains_status_and_report():
    class FakeCheckpoints:
        touched_paths = ["a.py", "b.py"]

    class FakeExecResult:
        tool_calls = 4

    class FakeResult:
        accepted = True
        checkpoints = FakeCheckpoints()
        evidence = {"evidence_score": 0.94}
        executor_result = FakeExecResult()
        report_path = "/tmp/run/report.md"

    block = render_result_block(FakeResult())
    assert "RESOLVED" in block
    assert "2 changed" in block
    assert "0.94" in block
    assert "/tmp/run/report.md" in block
