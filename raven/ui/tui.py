"""The TUI (plan §5.1): rich for output (panels, streaming, diffs) and
prompt_toolkit for input (multi-line, history). A thin view over
raven/session/manager.py's SessionManager — same slash commands, same
behaviour as the plain REPL, just richer rendering plus real approval
prompts and Ctrl-C handling wired up.

`should_use_tui` is a pure decision function (no imports of rich/
prompt_toolkit) so the launch-or-fallback logic is unit-testable without
mocking the import system — see tests/test_tui_fallback.py. Any failure
importing rich/prompt_toolkit, or actually running the TUI, must fall back
to raven/ui/repl.py's plain REPL rather than crash (plan §5.2)."""

from __future__ import annotations

from pathlib import Path


def rich_available() -> bool:
    try:
        import prompt_toolkit  # noqa: F401
        import rich  # noqa: F401
    except ImportError:
        return False
    return True


def should_use_tui(ui_mode: str, is_tty: bool, term: str | None, available: bool) -> bool:
    """Pure decision logic, matching plan §5.2's fallback rule:
    - ui_mode "off": never
    - ui_mode "on": always (even non-TTY — caller's explicit choice)
    - ui_mode "auto" (default): only if stdout is a TTY, TERM isn't "dumb",
      and rich/prompt_toolkit imported successfully
    """
    if ui_mode == "off":
        return False
    if ui_mode == "on":
        return True
    return bool(is_tty) and term != "dumb" and available


def run_tui(config, gateway, repo_root: Path | str = ".") -> int:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from rich.console import Console
    from rich.panel import Panel

    from raven.core import interrupt
    from raven.session.manager import SessionManager

    console = Console()
    repo_root = Path(repo_root)

    def approve(description: str, args: dict) -> bool:
        console.print(Panel(f"{description}\n\nargs: {args}", title="[yellow]Approval required[/yellow]"))
        try:
            choice = input("[y] allow once  [a] allow for session  [n] deny  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if choice == "a":
            session.approve_fn = lambda *_: True  # allow for the rest of this session
        return choice in ("y", "a")

    session = SessionManager(config, gateway, repo_root, approve_fn=approve)
    interrupt.install()
    prompt_session = PromptSession(history=InMemoryHistory())

    console.print(Panel(
        f"repo: {repo_root}\nmodel: {config.llm.model}",
        title="Raven", border_style="cyan",
    ))
    console.print("Type a message, or /help for commands. Ctrl-C to interrupt, /exit to quit.\n")

    while True:
        interrupt.reset()
        try:
            text = prompt_session.prompt(f"[{session.state.mode}] > ")
        except (EOFError, KeyboardInterrupt):
            console.print("Exiting.")
            return 0

        text = text.strip()
        if not text:
            continue
        if text in ("/exit", "/quit"):
            console.print("Exiting.")
            return 0

        try:
            reply = session.handle_input(text)
        except KeyboardInterrupt:
            console.print("[yellow]Interrupted — rolled back any partial changes from this command.[/yellow]")
            continue

        if reply:
            console.print(Panel(reply, title="Raven", border_style="green"))

        result = session.state.last_result
        if result is not None:
            console.print(
                f"[dim]run {result.run_id} · "
                f"{'RESOLVED' if result.accepted else 'UNRESOLVED'} · "
                f"tool calls {result.executor_result.tool_calls} · "
                f"tokens {gateway.stats.total_tokens}[/dim]\n"
            )

    return 0
