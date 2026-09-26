"""Centralized color palette (plan-approved visual identity). Every other
raven/ui/ module imports colors from here instead of hardcoding literals,
so the theme can change in one place. Values are plain hex/name strings
consumable by `rich` (Console/Style/markup) — rich degrades these to the
nearest 256- or 16-color automatically on terminals without true-color
support, so no separate low-color palette is needed."""

from __future__ import annotations

PRIMARY = "#a78bfa"     # bright violet -- the Raven accent
ACCENT = "#e879f9"      # fuchsia -- glyphs, prompt arrow, highlights
DEEP = "#7c3aed"        # deep violet -- badges and chips
BORDER = "#6d28d9"      # panel borders
BAR_BG = "#1e1033"      # background of the top/bottom bars
BAR_FG = "#c4b5fd"      # text on the bars
SECONDARY = "#7c6f9f"   # muted/soft purple
TEXT = "#e5e5e5"        # light gray / near-white
MUTED = "#6b7280"       # gray, for de-emphasized/secondary lines
SUCCESS = "#86efac"     # soft green, only for pass/fail signals
WARNING = "#fcd34d"
ERROR = "#f87171"
INFO = PRIMARY


def color_system_available() -> bool:
    """Best-effort check of whether the current stdout can render color at
    all. Callers should still just try rich output and let it degrade --
    this is only for deciding whether to bother building styled text vs.
    plain text in a hot path."""
    try:
        from rich.console import Console
        return Console().color_system is not None
    except Exception:
        return False
