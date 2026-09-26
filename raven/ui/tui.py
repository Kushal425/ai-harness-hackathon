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


def _top_bar(width: int, repo_root: Path, model: str):
    """Full-width header: a RAVEN badge, then repo and model on a dark bar."""
    from rich.text import Text

    from raven.ui import theme

    bar = Text()
    bar.append(" ◆ RAVEN ", style=f"bold #ffffff on {theme.DEEP}")
    bar.append("  repo ", style=f"{theme.SECONDARY} on {theme.BAR_BG}")
    bar.append(repo_root.name or str(repo_root), style=f"bold {theme.BAR_FG} on {theme.BAR_BG}")
    bar.append("   model ", style=f"{theme.SECONDARY} on {theme.BAR_BG}")
    bar.append(model, style=f"bold {theme.BAR_FG} on {theme.BAR_BG}")
    right = f"{repo_root}  "
    gap = width - bar.cell_len - len(right)
    if gap > 2:
        bar.append(" " * gap + right, style=f"{theme.MUTED} on {theme.BAR_BG}")
    else:
        bar.append(" " * max(0, width - bar.cell_len), style=f"on {theme.BAR_BG}")
    return bar


def _reply_panel(reply: str, command: str | None = None):
    """A Raven reply in a violet frame. Model replies render as Markdown;
    slash-command output is pre-formatted (aligned columns, diffs), so it's
    shown as-is -- Markdown would reflow its lines into one paragraph."""
    from rich.panel import Panel
    from rich.text import Text

    from raven.ui import theme

    try:
        if command == "/diff" and reply.lstrip().startswith("diff --git"):
            from rich.syntax import Syntax
            body = Syntax(reply, "diff", theme="ansi_dark", background_color="default")
        elif command:
            body = Text(reply)
        else:
            from rich.markdown import Markdown
            body = Markdown(reply)
    except Exception:
        body = Text(reply)
    return Panel(
        body, title=f"[bold {theme.ACCENT}]◆ raven[/]", title_align="left",
        border_style=theme.BORDER, padding=(0, 1),
    )


def run_tui(config, gateway, repo_root: Path | str = ".") -> int:
    import os
    import sys

    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.styles import Style
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
            safe_print(f"[{theme.ACCENT}]{glyph}[/] [{theme.SECONDARY}]{data.get('id')}[/]  {data.get('action')}")
        elif event == "verify_start":
            safe_print(f"[{theme.ACCENT}]◌[/] [{theme.MUTED}]verifying...[/]")
        elif event == "verify_done":
            safe_print(render_verification(data.get("evidence")))

    def approve(description: str, args: dict) -> bool:
        safe_print(Panel(
            f"{description}\n\nargs: {args}", title=f"[bold {theme.WARNING}]approval required[/]",
            title_align="left", border_style=theme.WARNING,
        ))
        try:
            choice = input("[y] allow once  [a] allow for session  [n] deny  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if choice == "a":
            session.approve_fn = lambda *_: True  # allow for the rest of this session
        return choice in ("y", "a")

    session = SessionManager(config, gateway, repo_root, approve_fn=approve, on_event=on_event)
    interrupt.install()
    prompt_style = Style.from_dict({
        "mode": f"bg:{theme.DEEP} #ffffff bold",
        "arrow": f"{theme.ACCENT} bold",
        "bottom-toolbar": f"noreverse bg:{theme.BAR_BG} {theme.BAR_FG}",
        "bottom-toolbar.key": f"noreverse bg:{theme.BAR_BG} {theme.ACCENT} bold",
        "bottom-toolbar.dim": f"noreverse bg:{theme.BAR_BG} {theme.SECONDARY}",
    })

    def bottom_toolbar():
        # Live status, re-rendered by prompt_toolkit on every redraw.
        return [
            ("class:bottom-toolbar.key", " ◆ "),
            ("class:bottom-toolbar", f"{session.state.mode}"),
            ("class:bottom-toolbar.dim", "  │  "),
            ("class:bottom-toolbar", config.llm.model),
            ("class:bottom-toolbar.dim", "  │  tokens "),
            ("class:bottom-toolbar", f"{gateway.stats.total_tokens:,}"),
            ("class:bottom-toolbar.dim", "  │  "),
            ("class:bottom-toolbar.key", "/help"),
            ("class:bottom-toolbar.dim", " commands  "),
            ("class:bottom-toolbar.key", "ctrl-c"),
            ("class:bottom-toolbar.dim", " interrupt  "),
            ("class:bottom-toolbar.key", "/exit"),
            ("class:bottom-toolbar.dim", " quit "),
        ]

    prompt_session = PromptSession(
        history=InMemoryHistory(), style=prompt_style, bottom_toolbar=bottom_toolbar,
    )

    if should_show_animation(sys.stdin.isatty() and sys.stdout.isatty(), os.environ.get("TERM"), "auto"):
        play_startup(console, config.llm.model)

    safe_print(_top_bar(console.width, repo_root, config.llm.model))
    safe_print(
        f"[{theme.MUTED}]Ask about the code, paste an issue, or try[/] "
        f"[{theme.ACCENT}]/plan[/] [{theme.MUTED}]·[/] [{theme.ACCENT}]/auto[/] "
        f"[{theme.MUTED}]·[/] [{theme.ACCENT}]/help[/]\n"
    )

    while True:
        interrupt.reset()
        try:
            text = prompt_session.prompt([
                ("class:mode", f" {session.state.mode} "),
                ("class:arrow", " ❯ "),
            ])
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
                safe_print(Panel(
                    render_result_block(result), title=f"[bold {theme.ACCENT}]◆ result[/]",
                    title_align="left", border_style=theme.BORDER if result.accepted else theme.ERROR,
                    padding=(0, 1),
                ))
            except Exception:
                if reply:
                    safe_print(_reply_panel(reply))
        elif reply:
            safe_print(_reply_panel(reply, command=text.split()[0] if text.startswith("/") else None))

        if result is not None and is_fresh_run:
            status_style = f"bold #0b0b0b on {theme.SUCCESS}" if result.accepted else f"bold #ffffff on {theme.ERROR}"
            safe_print(
                f"[{status_style}] {'RESOLVED' if result.accepted else 'UNRESOLVED'} [/]"
                f"[{theme.MUTED}]  run {result.run_id} · "
                f"tool calls {result.executor_result.tool_calls} · "
                f"tokens {gateway.stats.total_tokens:,}[/]"
            )
        safe_print("")

    return 0
