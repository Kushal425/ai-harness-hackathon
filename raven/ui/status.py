"""Agent-state glyph/label lookup (presentation only -- no new agent-side
state tracking). These are derived from signals that already exist:
the tool name being dispatched, ExecutorResult.aborted_reason, an
OrchestratorResult's Verdict, and the [recovery]-prefixed nudges already
produced by raven/recovery/handlers.py and threaded into history entries."""

from __future__ import annotations

# Glyphs used consistently across the TUI and the plain REPL/piped fallback
# (the fallback just drops the glyph and uses the bracketed [name] form).
GLYPH_PENDING = "◌"   # ◌
GLYPH_ACTIVE = "◉"    # ◉
GLYPH_DONE = "✓"      # ✓
GLYPH_FAILED = "✗"    # ✗
GLYPH_RECOVER = "↻"   # ↻

# Coarse state -> a short present-progressive label, used when we don't
# have a specific tool/step to name yet (e.g. right before the first model
# call of a run).
STATE_LABELS = {
    "thinking": "Thinking...",
    "exploring": "Analyzing repository...",
    "planning": "Planning implementation...",
    "executing": "Working...",
    "testing": "Running tests...",
    "verifying": "Verifying...",
    "recovering": "Recovering from a failed attempt...",
    "completed": "Repository analysis complete",
    "failed": "Failed",
}

# Tool name -> coarse state, so a live event stream can pick a sensible
# label without the executor needing to know anything about presentation.
_TOOL_TO_STATE = {
    "read": "exploring", "search": "exploring", "symbols": "exploring",
    "outline": "exploring", "git_status": "exploring", "git_diff": "exploring",
    "edit": "executing", "create": "executing", "shell": "executing",
    "tests": "testing",
}


def state_for_tool(tool_name: str) -> str:
    return _TOOL_TO_STATE.get(tool_name, "executing")


def label_for_state(state: str) -> str:
    return STATE_LABELS.get(state, state)


def glyph_for_tool_status(ok: bool | None) -> str:
    """ok=None means the tool call just started (still running)."""
    if ok is None:
        return GLYPH_ACTIVE
    return GLYPH_DONE if ok else GLYPH_FAILED
