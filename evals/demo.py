"""`make demo` -- the Crux moment, offline and reproducible.

Runs the Crux pipeline on tests/fixtures/crux_demo with the model's replies
scripted (evals/tasks/t15_*.yaml), so it works without an API key and shows
exactly the same thing every time:

  three candidate fixes all pass the visible tests and the issue's example
  -> running them against each other exposes the one input where they
     disagree -> one question, answered from repository evidence
  -> the plausible-but-wrong fix is rejected, and a hidden grader test
     confirms the choice.

With AI_API_KEY set, `make run ARGS="--repo tests/fixtures/crux_demo"` and
the same issue runs the identical pipeline against the live model.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.syntax import Syntax  # noqa: E402

from raven.config import RunSettings  # noqa: E402
from raven.core.orchestrator import run_orchestrator  # noqa: E402
from raven.crux.patching import applied  # noqa: E402
from raven.llm.fake import FakeClient  # noqa: E402
from raven.llm.gateway import LLMGateway  # noqa: E402
from raven.ui import theme  # noqa: E402
from raven.ui.render import render_crux_line, render_verification  # noqa: E402

TASK = REPO_ROOT / "evals" / "tasks" / "t15_crux_chunk_plausible_wrong_patch.yaml"


def hidden_test_passes(repo: Path, test_src: str) -> bool:
    (repo / "test_hidden_grader.py").write_text(test_src)
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_hidden_grader.py"],
                              cwd=repo, capture_output=True, text=True)
        return proc.returncode == 0
    finally:
        (repo / "test_hidden_grader.py").unlink(missing_ok=True)


def main() -> int:
    console = Console()
    task = yaml.safe_load(TASK.read_text())
    work = Path(tempfile.mkdtemp(prefix="raven-demo-")) / "listkit"
    shutil.copytree(REPO_ROOT / "tests" / "fixtures" / "crux_demo", work,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))

    console.print(Panel(task["goal"], title=f"[bold {theme.ACCENT}]◆ issue[/]", title_align="left",
                        border_style=theme.BORDER))
    console.print(f"[{theme.MUTED}]scripted model replies (offline demo) · repo: {work}[/]\n")

    client = FakeClient(list(task["scripted_responses"]))
    gateway = LLMGateway(client)
    result = run_orchestrator(gateway, work, task["goal"], strategy="crux",
                              settings=RunSettings(candidates=3, parallel_calls=False, trace=False, lessons=False),
                              on_event=lambda e, d: console.print(render_crux_line(d)) if e == "crux" else None)

    console.print()
    console.print(Panel(render_verification(result.evidence), title=f"[bold {theme.ACCENT}]◆ verification[/]",
                        title_align="left", border_style=theme.BORDER))
    diff = subprocess.run(["git", "diff", "--no-index", "--", str(REPO_ROOT / "tests/fixtures/crux_demo/listkit/chunks.py"),
                           str(work / "listkit" / "chunks.py")], capture_output=True, text=True).stdout
    console.print(Syntax(diff.split("\n", 4)[-1] if diff else "(no diff)", "diff", theme="ansi_dark",
                         background_color="default"))

    # The proof: what the hidden grader says about the selected fix vs the rejected one.
    hidden = task["check"]["hidden_test"]
    crux = result.evidence["crux"]
    rejected = [c for c in crux["candidates"] if c["status"] == "rejected-by-crux"]
    console.print(f"\n[bold {theme.PRIMARY}]hidden grader test[/] (never shown to Raven):")
    ok = hidden_test_passes(work, hidden)
    console.print(f"  selected {crux['winner']}: " + ("[bold green]PASS[/]" if ok else "[bold red]FAIL[/]"))
    if rejected:
        from raven.crux.patching import Edit, build_candidate
        import json
        raw = task["scripted_responses"][2 + int(rejected[0]["id"][1:]) - 1]
        edits = json.loads(raw.split("\n", 1)[1].rsplit("```", 1)[0])["edits"]
        checkpoint = (work / "listkit" / "chunks.py").read_text()
        (work / "listkit" / "chunks.py").write_text((REPO_ROOT / "tests/fixtures/crux_demo/listkit/chunks.py").read_text())
        cand = build_candidate(work, rejected[0]["id"], "", [Edit(**e) for e in edits])
        with applied(work, cand):
            bad = hidden_test_passes(work, hidden)
        (work / "listkit" / "chunks.py").write_text(checkpoint)
        console.print(f"  rejected {rejected[0]['id']}: " + ("[bold green]PASS[/]" if bad else "[bold red]FAIL[/]")
                      + f"  [{theme.MUTED}](passed every visible test — only the crux caught it)[/]")
    console.print(f"\n[{theme.MUTED}]{gateway.stats.calls} model calls · {gateway.stats.total_tokens:,} tokens "
                  f"(scripted) · report: {result.report_path}[/]")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
