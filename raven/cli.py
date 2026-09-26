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
from raven.core.orchestrator import run_orchestrator
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway
from raven.llm.providers import OpenAICompatibleClient
from raven.ui.repl import run_piped, run_repl
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
        print("[warn] AI_API_KEY not set — running against FakeClient (offline demo mode).")
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
        )
    return LLMGateway(client, max_retries=config.llm.max_retries, seed=config.llm.seed)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="raven", add_help=True)
    parser.add_argument("--repo", default=".", help="repository to act on (default: cwd)")
    parser.add_argument("--issue", default=None, help="task/issue text; runs the autonomous pipeline")
    parser.add_argument("--issue-file", default=None, help="path to a file containing the issue text")
    parser.add_argument(
        "--strategy", default=None, choices=["single_loop", "plan_execute", "delegated"],
        help="executor strategy override (default: config.yaml's executor.strategy)",
    )
    return parser.parse_args(argv)


def run_autonomous(config, gateway, repo_root: Path, goal: str, strategy: str | None) -> int:
    strategy = strategy or config.raw.get("executor", {}).get("strategy", "single_loop")
    print(f"[raven] repo={repo_root}  strategy={strategy}  model={config.llm.model}")
    print("[raven] running...")

    result = run_orchestrator(gateway, repo_root, goal, mode="autonomous", strategy=strategy)

    print("\n===== RAVEN RESULT =====")
    print(f"run_id:   {result.run_id}")
    print(f"accepted: {result.accepted}")
    print(f"reason:   {result.reason}")
    if result.evidence:
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
    gateway = build_gateway(config)

    issue_text = args.issue
    if args.issue_file:
        issue_text = Path(args.issue_file).read_text().strip()

    # Resolve once, here, to an absolute path -- args.repo defaults to "."
    # and every caller must agree on what that means (relative to the cwd
    # this process actually started in). Resolving late/differently per
    # code path is what caused the TUI banner to show the literal string
    # "." instead of confirming which directory it's really targeting.
    repo_root = Path(args.repo).resolve()

    try:
        if issue_text:
            return run_autonomous(config, gateway, repo_root, issue_text, args.strategy)
        if sys.stdin.isatty():
            return run_interactive(config, gateway, repo_root)
        piped_text = sys.stdin.read().strip()
        if not piped_text:
            return run_interactive(config, gateway, repo_root)
        return run_piped(config, gateway, piped_text)
    finally:
        gateway.close()


if __name__ == "__main__":
    raise SystemExit(main())
