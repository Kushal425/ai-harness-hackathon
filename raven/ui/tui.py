"""The TUI (plan §5.1): rich for output (panels, streaming, diffs, the
Raven theme/mascot) and prompt_toolkit for input (multi-line, history). A
thin view over raven/session/manager.py's SessionManager — same slash
commands, same behaviour as the plain REPL, just richer rendering plus
real approval prompts, Ctrl-C handling, and a live tool-call stream.

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
    import os
    import sys

    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from rich.console import Console
    from rich.panel import Panel

    from raven.core import interrupt
    from raven.session.manager import SessionManager
    from raven.ui import theme
    from raven.ui.animations import play_startup, should_show_animation
    from raven.ui.render import render_result_block, render_tool_line, render_verification

    console = Console()
    repo_root = Path(repo_root)

    def safe_print(*args, **kwargs) -> None:
        # Rendering must never abort a run -- degrade to a plain print, or
        # to nothing at all, rather than propagate.
        try:
            console.print(*args, **kwargs)
        except Exception:
            try:
                print(*[str(a) for a in args])
            except Exception:
                pass

    def on_event(event: str, data: dict) -> None:
        if event in ("tool_start", "tool_end"):
            ok = data.get("ok") if event == "tool_end" else None
            safe_print(render_tool_line(data.get("tool", ""), data.get("args", {}), ok))
        elif event == "plan_step":
            glyph = {"active": "◉", "done": "✓", "failed": "✗"}.get(data.get("status"), "○")
            safe_print(f"{glyph} {data.get('id')}  {data.get('action')}", style=theme.SECONDARY)
        elif event == "verify_start":
            safe_print("◌ Verifying...", style=theme.MUTED)
        elif event == "verify_done":
            safe_print(render_verification(data.get("evidence")))

    def approve(description: str, args: dict) -> bool:
        safe_print(Panel(f"{description}\n\nargs: {args}", title="[yellow]Approval required[/yellow]"))
        try:
            choice = input("[y] allow once  [a] allow for session  [n] deny  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if choice == "a":
            session.approve_fn = lambda *_: True  # allow for the rest of this session
        return choice in ("y", "a")

    session = SessionManager(config, gateway, repo_root, approve_fn=approve, on_event=on_event)
    interrupt.install()
    prompt_session = PromptSession(history=InMemoryHistory())

    if should_show_animation(sys.stdin.isatty() and sys.stdout.isatty(), os.environ.get("TERM"), "auto"):
        play_startup(console, config.llm.model)

    safe_print(Panel(
        f"repo: {repo_root}\nmodel: {config.llm.model}",
        title="Raven", border_style=theme.PRIMARY,
    ))
    safe_print("Type a message, or /help for commands. Ctrl-C to interrupt, /exit to quit.\n")

    while True:
        interrupt.reset()
        try:
            text = prompt_session.prompt(f"[{session.state.mode}] ❯ ")
        except (EOFError, KeyboardInterrupt):
            safe_print("Exiting.")
            return 0

        text = text.strip()
        if not text:
            continue
        if text in ("/exit", "/quit"):
            safe_print("Exiting.")
            return 0

        run_id_before = session.state.last_result.run_id if session.state.last_result else None
        try:
            reply = session.handle_input(text)
        except KeyboardInterrupt:
            safe_print("Interrupted — rolled back any partial changes from this command.", style=theme.WARNING)
            continue

        result = session.state.last_result
        is_fresh_run = result is not None and result.run_id != run_id_before

        if is_fresh_run:
            try:
                safe_print(Panel(render_result_block(result), title="RAVEN RESULT", border_style=theme.PRIMARY))
            except Exception:
                if reply:
                    safe_print(Panel(reply, title="Raven", border_style=theme.SUCCESS))
        elif reply:
            safe_print(Panel(reply, title="Raven", border_style=theme.SUCCESS))

        if result is not None:
            safe_print(
                f"run {result.run_id} · "
                f"{'RESOLVED' if result.accepted else 'UNRESOLVED'} · "
                f"tool calls {result.executor_result.tool_calls} · "
                f"tokens {gateway.stats.total_tokens}",
                style=theme.MUTED,
            )
            safe_print("")

    return 0
