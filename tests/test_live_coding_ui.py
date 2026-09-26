"""The live "watch it code" UI: tools report the diff they made, the
executor and Crux emit it (plus a 'working' signal before slow steps), and
the renderer types it out within a fixed time budget."""

import io
import shutil
import time
from pathlib import Path

from rich.console import Console

from raven.tools.edit import edit
from raven.tools.fs import create
from raven.tools.registry import RunContext
from raven.ui.render import diff_lines, diff_stats, type_out

TOY = Path(__file__).parent / "fixtures" / "toy_repo"


def test_edit_and_create_report_their_diff(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\ny = 2\n")
    ctx = RunContext(repo_root=tmp_path, mode="autonomous")
    diff = edit(ctx, "m.py", "x = 1", "x = 10").data["diff"]
    assert "-x = 1" in diff and "+x = 10" in diff
    assert diff_stats(diff) == (["m.py"], 1, 1)
    new = create(ctx, "n.py", "a = 1\nb = 2\n").data["diff"]
    assert diff_stats(new) == (["n.py"], 2, 0)


def test_diff_lines_style_gutters_and_truncate():
    diff = "--- a/m.py\n+++ b/m.py\n@@ -3,2 +3,2 @@ def f():\n     keep\n-old\n+new\n" + "+more\n" * 30
    lines = [t.plain for t in diff_lines(diff, max_lines=5)]
    assert lines[0].strip() == "⋯ line 3  def f():"
    assert lines[2].strip() == "− old" and lines[3].strip() == "+ new"
    assert lines[-1].strip() == "… 29 more lines"


def test_type_out_is_capped_in_time():
    out = io.StringIO()
    console = Console(file=out, width=100)
    diff = "--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n" + "+line\n" * 200
    start = time.monotonic()
    type_out(console.print, "c1", diff, max_lines=40, budget_s=0.3)
    assert time.monotonic() - start < 1.0
    assert "c1 · m.py" in out.getvalue() and "+200" in out.getvalue()


def test_agent_loop_emits_working_and_the_edit_diff(tmp_path):
    from raven.core.orchestrator import run_orchestrator
    from raven.config import RunSettings
    from raven.llm.fake import FakeClient
    from raven.llm.gateway import LLMGateway

    repo = tmp_path / "repo"
    shutil.copytree(TOY, repo)
    script = ['```action\n{"tool": "edit", "args": {"path": "calc/arithmetic.py", "search": '
              '"return sum(numbers) / (len(numbers) + 1)", "replace": "return sum(numbers) / len(numbers)"}}\n```',
              '```action\n{"tool": "done", "args": {"summary": "fixed"}}\n```']
    events = []
    run_orchestrator(LLMGateway(FakeClient(script)), repo, "fix the failing average() test", strategy="single_loop",
                     reproduce=False, settings=RunSettings(trace=False, lessons=False),
                     on_event=lambda e, d: events.append((e, d)))
    assert ("working", {"text": "thinking…"}) in events
    [edit_event] = [d for e, d in events if e == "tool_end" and d["tool"] == "edit"]
    assert "+    return sum(numbers) / len(numbers)" in edit_event["diff"]


def test_crux_emits_candidate_and_selected_diffs(tmp_path):
    from tests.test_crux import CAND_A, CAND_B, CAND_C, DEMO, ISSUE, LOCALIZE, PROBE, VERDICT
    from raven.crux.pipeline import run_crux
    from raven.llm.fake import FakeClient
    from raven.llm.gateway import LLMGateway

    repo = tmp_path / "repo"
    shutil.copytree(DEMO, repo)
    events = []
    run_crux(LLMGateway(FakeClient([LOCALIZE, PROBE, CAND_A, CAND_B, CAND_C, VERDICT])), repo, ISSUE, k=3,
             parallel=False, on_event=lambda e, d: events.append((e, d)))
    working = [d["text"] for e, d in events if e == "working"]
    assert working[:3] == ["reading the issue and locating the code…", "writing a probe that reproduces the issue…",
                           "writing 3 candidate fixes…"]
    diffs = {d["id"]: d["diff"] for e, d in events if e == "crux" and d.get("stage") == "candidate"}
    assert set(diffs) == {"c1", "c2", "c3"} and all("+++ b/listkit/chunks.py" in v for v in diffs.values())
    [select] = [d for e, d in events if e == "crux" and d.get("stage") == "select"]
    assert "range(0, len(items), size)" in select["diff"]
