"""Plain REPL — the fallback conversation UI (plan §5.2). Must work with no
TTY niceties: the TUI (raven/ui/tui.py) is the primary interface, but the
harness must always work in a plain terminal even if that layer fails or is
absent. This is a thin view over raven/session/manager.py's SessionManager —
all mode/slash-command logic lives there exactly once, so behaviour is
identical between this and the TUI."""

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


def run_repl(config: RavenConfig, gateway: LLMGateway, repo_root: Path | str = ".") -> int:
    print(BANNER.format(model=config.llm.model))
    session = SessionManager(config, gateway, Path(repo_root))

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
