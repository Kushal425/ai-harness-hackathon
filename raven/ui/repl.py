"""Plain REPL — the fallback conversation UI (plan §5.2). Must work with no
TTY niceties: a rich/prompt_toolkit TUI comes in Phase 2, but the harness
must always work in a plain terminal even if that layer fails or is absent."""

from __future__ import annotations

from raven.config import RavenConfig
from raven.llm.gateway import LLMGateway
from raven.llm.protocol import Message

SYSTEM_PROMPT = (
    "You are Raven, a conversational coding-agent harness. "
    "You are currently running in a minimal chat-only mode: no repository "
    "tools are wired up yet. Answer plainly and say so if asked to edit code."
)

BANNER = """\
=========================================
  Raven  ·  model: {model}
=========================================
Type a message and press Enter. Ctrl-C or /exit to quit.
"""


def run_repl(config: RavenConfig, gateway: LLMGateway) -> int:
    print(BANNER.format(model=config.llm.model))
    history: list[Message] = [Message(role="system", content=SYSTEM_PROMPT)]

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            return 0

        if not user_input:
            continue
        if user_input in ("/exit", "/quit"):
            print("Exiting.")
            return 0

        history.append(Message(role="user", content=user_input))

        print("Raven: ", end="", flush=True)
        try:
            result = gateway.complete(
                history, stream=True, on_token=lambda tok: print(tok, end="", flush=True)
            )
            print()
        except Exception as exc:
            print(f"\n[error] {exc}")
            continue

        history.append(Message(role="assistant", content=result.text))

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
