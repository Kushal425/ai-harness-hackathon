"""Cooperative Ctrl-C handling (plan §3, §10): the executor checks a flag
once per turn — cheap, safe, no threading — rather than being interrupted
mid-statement. A SIGINT handler is installed only by the interactive
entrypoints (raven/ui/tui.py, and optionally the REPL); the autonomous CLI
path, the eval harness, and every test never install it, so Ctrl-C there
keeps Python's default behaviour. A second Ctrl-C while the first is still
being handled raises immediately — a deliberate hard exit."""

from __future__ import annotations

import signal

_interrupted = False
_installed = False


def _handler(signum, frame) -> None:
    global _interrupted
    if _interrupted:
        raise KeyboardInterrupt("second interrupt — exiting")
    _interrupted = True


def install() -> None:
    global _installed
    if not _installed:
        signal.signal(signal.SIGINT, _handler)
        _installed = True


def reset() -> None:
    global _interrupted
    _interrupted = False


def is_interrupted() -> bool:
    return _interrupted
