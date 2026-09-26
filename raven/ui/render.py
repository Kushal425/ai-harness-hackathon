"""Shared rendering components (pure functions -- no printing, no side
effects). Each takes plain data already produced by the core system
(ExecutorResult, OrchestratorResult, Plan, an evidence dict) and returns a
`rich` renderable or plain string, so these are unit-testable by rendering
to an in-memory Console and asserting on the captured text -- no real
terminal needed. Every caller (raven/ui/tui.py, repl.py) must still wrap
calls into these in a try/except: a rendering bug must degrade to plain
text, never abort a run (see raven/tools/registry.py, raven/llm/gateway.py
for the same "degrade never crash" pattern used throughout this codebase)."""

from __future__ import annotations

from raven.ui import theme
from raven.ui.status import glyph_for_tool_status

_TOOL_COL_WIDTH = 10


def short_arg_summary(tool: str, args: dict) -> str:
    """One short, human-scannable token describing what a tool call
    targeted -- never a payload dump (plan: 'do not dump huge JSON
    payloads into the terminal')."""
    if not args:
        return ""
    for key in ("path", "pattern", "cmd", "target", "query"):
        if key in args and args[key]:
            value = str(args[key])
            return value if len(value) <= 70 else value[:67] + "..."
    # fall back to the first arg value, same truncation
    value = str(next(iter(args.values())))
    return value if len(value) <= 70 else value[:67] + "..."


def render_tool_line_text(tool: str, args: dict, ok: bool | None) -> str:
    """Plain-text form (no color codes) -- used by both the TUI (styled on
    top of this) and the plain REPL/piped fallback (used as-is)."""
    glyph = glyph_for_tool_status(ok)
    connector = "├─" if ok is None else glyph
    name = tool.ljust(_TOOL_COL_WIDTH)
    summary = short_arg_summary(tool, args)
    return f"{connector} {name} {summary}".rstrip()


def render_tool_line(tool: str, args: dict, ok: bool | None):
    """Rich-styled version of render_tool_line_text for the TUI."""
    from rich.text import Text

    line = render_tool_line_text(tool, args, ok)
    if ok is True:
        return Text(line, style=theme.SUCCESS)
    if ok is False:
        return Text(line, style=theme.ERROR)
    return Text(line, style=theme.MUTED)


def render_plan(plan) -> str:
    """Builds on Plan.as_text() (raven/core/planner.py) rather than
    replacing its step-status marks -- just the canonical plan view."""
    if plan is None or not plan.steps:
        return "(no plan)"
    return "Plan\n\n" + plan.as_text()


def render_verification(evidence: dict | None):
    """A verification panel framed as engineering evidence, not a
    gamified score -- no stars, no emoji progress bars, no color-coded
    0-100 meter."""
    from rich.text import Text

    if not evidence:
        return Text("(no verification evidence available)", style=theme.MUTED)

    lines = ["Verification\n"]
    checks = [
        ("repro_fixed", "Original issue reproduced and fixed"),
        ("no_new_failures", "No new test failures introduced"),
    ]
    for key, label in checks:
        value = evidence.get(key)
        if value is None:
            continue
        glyph = "✓" if value else "✗"
        lines.append(f"{glyph} {label}")

    repro = evidence.get("repro")
    if repro:
        lines.append(f"{'✓' if repro.get('failed_before') else '✗'} Reproduction test fails on the original code")
        lines.append(f"{'✓' if repro.get('passes_after') else '✗'} Reproduction test passes on the fix")

    score = evidence.get("evidence_score")
    if score is not None:
        lines.append(f"\nEvidence score: {score:.2f}")

    collateral = evidence.get("collateral_changes")
    if collateral:
        lines.append(f"\n! {len(collateral)} collateral behavior change(s) detected")

    return Text("\n".join(lines))


def render_result_block(result) -> str:
    """The final boxed summary text (caller wraps in a rich.Panel for the
    TUI, or prints between ===== markers for the plain/piped path)."""
    status = "RESOLVED" if result.accepted else "UNRESOLVED"
    files_changed = len(result.checkpoints.touched_paths) if result.checkpoints else 0
    evidence_score = (result.evidence or {}).get("evidence_score")
    score_line = f"{evidence_score:.2f}" if evidence_score is not None else "n/a"
    tool_calls = result.executor_result.tool_calls if result.executor_result else 0

    lines = [
        f"Status       {status}",
        f"Files        {files_changed} changed",
        f"Tool calls   {tool_calls}",
        f"Evidence     {score_line}",
        "",
        f"Report: {result.report_path}",
    ]
    return "\n".join(lines)
