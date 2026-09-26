"""Plain REPL — the fallback conversation UI (plan §5.2). Must work with no
TTY niceties: the TUI (raven/ui/tui.py) is the primary interface, but the
harness must always work in a plain terminal even if that layer fails or is
absent. This is a thin view over raven/session/manager.py's SessionManager —
all mode/slash-command logic lives there exactly once, so behaviour is
identical between this and the TUI. Tool-call events render as plain
bracketed lines (no color, no rich) — matches under TERM=dumb."""

from __future__ import annotations

import io
import os
import select
import sys
from pathlib import Path

from raven.config import RavenConfig
from raven.llm.gateway import LLMGateway
from raven.intake import ask_for_target_repo, is_harness_repo
from raven.session.manager import SessionManager


BANNER = """\
=========================================
  Raven  ·  model: {model}
=========================================
Type a message and press Enter, or /help for commands. Ctrl-C or /exit to quit.
"""


def plain_event_printer(event: str, data: dict) -> None:
    """Bracketed, colorless event lines for TERM=dumb / piped / autonomous
    output — see raven/ui/render.py's render_tool_line_text for the shared
    truncation logic this borrows the spirit of, kept deliberately simpler
    here since there's no status glyph column to line up."""
    from raven.ui.render import short_arg_summary

    if event == "tool_start":
        tool = data.get("tool", "")
        summary = short_arg_summary(tool, data.get("args", {}))
        print(f"[{tool}] {summary}".rstrip())
    elif event == "tool_end":
        if not data.get("ok", True):
            print(f"[{data.get('tool', '')}] failed")
    elif event == "notice":
        print(f"[raven] {data.get('text', '')}")
    elif event == "crux":
        from raven.ui.render import render_crux_text
        print(render_crux_text(data))
    elif event == "plan_step":
        status = data.get("status")
        if status in ("done", "failed"):
            mark = "done" if status == "done" else "FAILED"
            print(f"[plan] {data.get('id')} {data.get('action')} -- {mark}")
    elif event == "verify_start":
        print("[verify] running...")
    elif event == "verify_done":
        evidence = data.get("evidence") or {}
        score = evidence.get("evidence_score")
        if score is not None:
            print(f"[verify] evidence score {score:.2f}")


def read_message(prompt: str, settle_s: float = 0.05) -> str:
    """One message, even when it's a multi-line paste: after the first
    line, whatever else arrives within `settle_s` (the rest of the paste)
    is part of the same message. Plain input() would turn every pasted
    line of an issue into a separate message."""
    lines = [input(prompt)]
    try:
        fd = sys.stdin.fileno()
        pending = b""
        while select.select([fd], [], [], settle_s)[0]:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            pending += chunk
        if pending:
            lines.extend(pending.decode(errors="replace").splitlines())
    except (OSError, ValueError, io.UnsupportedOperation):
        pass
    return "\n".join(lines)


def run_repl(config: RavenConfig, gateway: LLMGateway, repo_root: Path | str = ".") -> int:
    print(BANNER.format(model=config.llm.model))
    session = SessionManager(config, gateway, Path(repo_root), on_event=plain_event_printer)
    if is_harness_repo(session.state.repo_root):
        answer = ask_for_target_repo(input, print)
        if answer:
            print(session.handle_input(f"/repo {answer}"))

    while True:
        try:
            user_input = read_message(f"[{session.state.mode}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return 0

        if not user_input:
            continue
        if user_input in ("/exit", "/quit"):
            print("Exiting.")
            return 0

        reply = session.handle_input(user_input)
        if reply:
            print(f"Raven: {reply}")

    return 0
