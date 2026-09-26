"""CLI entry point. Loads config, reads AI_API_KEY, builds the gateway
(FakeClient if AI_API_KEY is unset — offline dev/demo — else the real
OpenAI-compatible client), probes tool-calling support, and hands off to
the REPL (interactive TTY) or the piped path (stdin/non-TTY)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from raven.config import load_config
from raven.core.judge import outcome_label
from raven.core.orchestrator import run_orchestrator
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway
from raven.llm.providers import OpenAICompatibleClient
from raven.intake import REPO_HELP, clone_repo, expand_issue_refs, is_harness_repo, looks_like_git_url, resolve_repo
from raven.ui.repl import plain_event_printer, run_repl
from raven.ui.tui import rich_available, run_tui, should_use_tui


def run_interactive(config, gateway, repo_root: Path) -> int:
    """Picks the TUI or the plain REPL per config `ui.tui` (plan §5.2).
    Any failure launching or running the TUI falls back to the plain REPL
    rather than crashing — the harness must always work in a plain
    terminal."""
    ui_mode = config.raw.get("ui", {}).get("tui", "auto")
    if should_use_tui(ui_mode, sys.stdin.isatty() and sys.stdout.isatty(), os.environ.get("TERM"), rich_available()):
        try:
            return run_tui(config, gateway, repo_root)
        except Exception as exc:
            print(f"[warn] TUI failed to start ({exc}); falling back to the plain REPL.")
    return run_repl(config, gateway, repo_root)


def build_gateway(config) -> LLMGateway:
    if not config.llm.api_key:
        # interactive only (main() refuses scored runs without a key)
        print("[warn] AI_API_KEY not set — OFFLINE DEMO MODE: replies come from a scripted fake model.")
        client = FakeClient()
    else:
        client = OpenAICompatibleClient(
            base_url=config.llm.base_url,
            api_key=config.llm.api_key,
            model=config.llm.model,
            temperature=config.llm.temperature,
            seed=config.llm.seed,
            max_output_tokens=config.llm.max_output_tokens,
            timeout_s=config.llm.request_timeout_s,
            tool_protocol=config.llm.tool_protocol,
        )
    return LLMGateway(client, max_retries=config.llm.max_retries, seed=config.llm.seed)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="raven", add_help=True)
    parser.add_argument(
        "--repo", default=None,
        help="repository to work on: a path or a git URL to clone (default: $RAVEN_REPO, else cwd)",
    )
    parser.add_argument("--issue", default=None, help="task/issue text; runs the autonomous pipeline")
    parser.add_argument("--issue-file", default=None, help="path to a file containing the issue text")
    parser.add_argument(
        "--strategy", default=None, choices=["crux", "single_loop", "plan_execute", "delegated"],
        help="executor strategy override (default: config.yaml's executor.strategy)",
    )
    return parser.parse_args(argv)


def run_autonomous(config, gateway, repo_root: Path, goal: str, strategy: str | None) -> int:
    strategy = strategy or config.raw.get("executor", {}).get("strategy", "crux")
    print(f"[raven] repo={repo_root}  strategy={strategy}  model={config.llm.model}")
    print("[raven] running...")

    # Plain bracketed event stream -- no animation, no color, no delay.
    # This is the hackathon scoring path; it must never wait on rendering.
    result = run_orchestrator(
        gateway, repo_root, goal, mode="autonomous", strategy=strategy,
        on_event=plain_event_printer, **config.run_kwargs(),
    )

    print("\n===== RAVEN RESULT =====")
    print(f"run_id:   {result.run_id}")
    print(f"accepted: {result.accepted}")
    print(f"outcome:  {outcome_label(result.accepted, result.verified)}")
    print(f"reason:   {result.reason}")
    if result.evidence:
        repro = result.evidence.get("repro")
        if repro:
            print(f"repro:    fails before fix: {repro['failed_before']}  ·  passes after: {repro['passes_after']}")
        print(f"evidence: {result.evidence}")
    print(f"report:   {result.report_path}")

    from raven.tools.git_tool import is_repo_toplevel
    if is_repo_toplevel(repo_root):
        diff = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--stat"], capture_output=True, text=True
        )
        if diff.returncode == 0 and diff.stdout.strip():
            print("\nfiles changed:")
            print(diff.stdout.strip())
    print("===== END =====")

    return 0 if result.accepted else 1


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    args = _parse_args(argv)
    config = load_config()

    # Every non-interactive way of supplying the task goes through the SAME
    # autonomous pipeline (plan §14.2): --issue, --issue-file, RAVEN_ISSUE,
    # or piped stdin.
    issue_text = args.issue
    if args.issue_file:
        issue_text = Path(args.issue_file).read_text().strip()
    if not issue_text and os.environ.get("RAVEN_ISSUE", "").strip():
        issue_text = os.environ["RAVEN_ISSUE"].strip()
    interactive = sys.stdin.isatty()
    if not issue_text and not interactive:
        issue_text = sys.stdin.read().strip()
        interactive = not issue_text and sys.stdout.isatty()

    # Resolved once, to an absolute path: --repo, then RAVEN_REPO, then cwd.
    repo_root = resolve_repo(args.repo)
    if issue_text and looks_like_git_url(args.repo or ""):
        repo_root = clone_repo(args.repo)

    if issue_text or not interactive:
        if not config.llm.api_key:
            # a scored run must never quietly "succeed" against the fake model
            print("[raven] AI_API_KEY is not set. Export it, then re-run:\n"
                  "  export AI_API_KEY=\"<key>\"", file=sys.stderr)
            return 2
        if is_harness_repo(repo_root):
            print("[raven] No target repository: Raven was started inside its own source tree.\n"
                  + REPO_HELP, file=sys.stderr)
            return 2
        if not issue_text:
            print("[raven] No task given. Pipe it in, or pass --issue / --issue-file / RAVEN_ISSUE.",
                  file=sys.stderr)
            return 2

    gateway = build_gateway(config)
    try:
        if issue_text:
            return run_autonomous(config, gateway, repo_root, expand_issue_refs(issue_text), args.strategy)
        return run_interactive(config, gateway, repo_root)
    finally:
        gateway.close()


if __name__ == "__main__":
    raise SystemExit(main())
