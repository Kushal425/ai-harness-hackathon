"""Plain REPL — the fallback conversation UI (plan §5.2). Must work with no
TTY niceties: the TUI (raven/ui/tui.py) is the primary interface, but the
harness must always work in a plain terminal even if that layer fails or is
absent. This is a thin view over raven/session/manager.py's SessionManager —
all mode/slash-command logic lives there exactly once, so behaviour is
identical between this and the TUI. Tool-call events render as plain
bracketed lines (no color, no rich) — matches under TERM=dumb."""

from __future__ import annotations

from pathlib import Path

from raven.config import RavenConfig
from raven.llm.gateway import LLMGateway
from raven.llm.protocol import Message
from raven.session.manager import SessionManager

SYSTEM_PROMPT = (
    "You are Raven, a conversational coding-agent harness. "
    "Answer plainly; say so if asked to edit code outside of /plan or /auto."
)

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


def run_repl(config: RavenConfig, gateway: LLMGateway, repo_root: Path | str = ".") -> int:
    print(BANNER.format(model=config.llm.model))
    session = SessionManager(config, gateway, Path(repo_root), on_event=plain_event_printer)

    while True:
        try:
            user_input = input(f"[{session.state.mode}] > ").strip()
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


def run_piped(config: RavenConfig, gateway: LLMGateway, text: str) -> int:
    """Non-interactive path: stdin was piped rather than a TTY. Sends the
    whole input as one user message and prints the reply, then exits 0."""
    history = [
        Message(role="system", content=SYSTEM_PROMPT),
        Message(role="user", content=text),
    ]
    try:
        result = gateway.complete(history, stream=False)
    except Exception as exc:
        print(f"[error] {exc}")
        return 1
    print("===== RAVEN RESULT =====")
    print(result.text)
    print("===== END =====")
    return 0
