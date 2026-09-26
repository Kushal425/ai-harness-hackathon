"""Startup sequence (~1-1.5s hard cap). `should_show_animation` is pure
decision logic (no imports of rich/time.sleep), unit-testable the same way
raven/ui/tui.py's should_use_tui is -- see tests/test_animations.py. The
actual `play_startup` is best-effort presentation: any failure falls back
to printing the banner once, plainly, and never raises."""

from __future__ import annotations

import time

from raven.ui.assets import RAVEN_BANNER, RAVEN_COMPACT, RAVEN_WORDMARK

_CHECKLIST = ["config", "workspace", "gateway", "tools"]
_STAGGER_S = 0.06
_MAX_TOTAL_S = 1.5
_SWEEP_FRAMES = 16
_FRAME_S = 0.03
_WIDE_MIN_COLS = 72

# deep violet -> Raven violet -> fuchsia -> soft pink
_GRADIENT = [(0x7c, 0x3a, 0xed), (0xa7, 0x8b, 0xfa), (0xe8, 0x79, 0xf9), (0xf0, 0xab, 0xfc)]
_SHINE = (0xff, 0xff, 0xff)


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


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _gradient(t: float) -> tuple:
    t = min(max(t, 0.0), 1.0) * (len(_GRADIENT) - 1)
    i = min(int(t), len(_GRADIENT) - 2)
    return _lerp(_GRADIENT[i], _GRADIENT[i + 1], t - i)


def _layout(wide: bool, model: str) -> list[str]:
    """Raven on the left; wordmark (or plain name) and tagline beside it."""
    raven = (RAVEN_BANNER if wide else RAVEN_COMPACT).splitlines()
    pad = max(len(line) for line in raven) + 3
    if wide:
        side = {2 + i: line for i, line in enumerate(RAVEN_WORDMARK.splitlines())}
        side[9] = "AI Coding Harness  ·  " + model
        side[10] = "the three-eyed raven sees all"
    else:
        side = {2: "R A V E N", 4: "AI Coding Harness", 5: model}
    rows = max(len(raven), max(side) + 1)
    return [
        (raven[r] if r < len(raven) else "").ljust(pad) + side.get(r, "")
        for r in range(rows)
    ]


def _frame(lines: list[str], sweep: float | None):
    """One colored frame. Color runs diagonally across the lockup; `sweep`
    is the position of a bright diagonal glint (None = no glint)."""
    from rich.text import Text

    span = max(len(line) for line in lines) + len(lines)
    text = Text()
    for r, line in enumerate(lines):
        for c, ch in enumerate(line):
            if ch == " ":
                text.append(ch)
                continue
            d = c + r * 2
            rgb = _gradient(d / span)
            if sweep is not None and abs(d - sweep) < 4:
                rgb = _lerp(rgb, _SHINE, 1 - abs(d - sweep) / 4)
            text.append(ch, style="#%02x%02x%02x" % rgb)
        text.append("\n")
    return text


def play_startup(console, model: str) -> None:
    """Plays the startup sequence on an already-constructed rich Console.
    Caller (raven/ui/tui.py) is responsible for calling should_show_animation
    first -- this function assumes it's safe to animate and just does its
    best, degrading to a single static print on any error."""
    try:
        from rich.live import Live
        from rich.text import Text

        start = time.monotonic()
        lines = _layout(console.width >= _WIDE_MIN_COLS, model)
        span = max(len(line) for line in lines) + len(lines) * 2
        console.print()
        with Live(_frame(lines, None), console=console, refresh_per_second=60, transient=False) as live:
            for i in range(_SWEEP_FRAMES + 1):
                if time.monotonic() - start > _MAX_TOTAL_S / 2:
                    break
                live.update(_frame(lines, -4 + (span + 8) * i / _SWEEP_FRAMES))
                time.sleep(_FRAME_S)
            live.update(_frame(lines, None))

        console.print("  ", end="")
        for item in _CHECKLIST:
            if time.monotonic() - start > _MAX_TOTAL_S:
                break
            console.print(Text("◆ ", style="#e879f9").append(item + "   ", style="#a78bfa"), end="")
            console.file.flush()
            time.sleep(_STAGGER_S)
        console.print(Text("ready", style="bold #f0abfc"))
        console.print()
    except Exception:
        try:
            console.print(f"RAVEN · model: {model}")
        except Exception:
            pass  # presentation must never block or crash the run
