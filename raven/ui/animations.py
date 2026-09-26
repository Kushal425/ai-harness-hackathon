"""Startup sequence (~1-1.5s hard cap). `should_show_animation` is pure
decision logic (no imports of rich/time.sleep), unit-testable the same way
raven/ui/tui.py's should_use_tui is -- see tests/test_animations.py. The
actual `play_startup` is best-effort presentation: any failure falls back
to printing the banner once, plainly, and never raises."""

from __future__ import annotations

import time

from raven.ui.assets import RAVEN_BANNER, RAVEN_COMPACT

_CHECKLIST = ["Configuration", "Workspace", "Model gateway", "Tool registry"]
_STAGGER_S = 0.08
_MAX_TOTAL_S = 1.5


def should_show_animation(is_tty: bool, term: str | None, ui_mode: str) -> bool:
    """Never for: ui_mode 'off', a non-TTY (piped/autonomous), or TERM
    'dumb'. This must be checked BEFORE any sleep call happens -- the
    autonomous evaluation path must never wait through a splash screen."""
    if ui_mode == "off":
        return False
    if not is_tty:
        return False
    if term == "dumb":
        return False
    return True


def play_startup(console, model: str) -> None:
    """Plays the startup sequence on an already-constructed rich Console.
    Caller (raven/ui/tui.py) is responsible for calling should_show_animation
    first -- this function assumes it's safe to animate and just does its
    best, degrading to a single static print on any error."""
    try:
        art = RAVEN_BANNER if console.width >= 60 else RAVEN_COMPACT
        console.print(art, style="#a78bfa", highlight=False)
        console.print("RAVEN", style="bold #a78bfa", justify="left")
        console.print("AI Coding Harness\n", style="#7c6f9f")

        start = time.monotonic()
        for item in _CHECKLIST:
            if time.monotonic() - start > _MAX_TOTAL_S:
                break
            console.print(f"  ✓ {item}", style="green")
            time.sleep(_STAGGER_S)
        console.print("\nReady.\n", style="bold green")
    except Exception:
        try:
            console.print(f"RAVEN · model: {model}")
        except Exception:
            pass  # presentation must never block or crash the run
