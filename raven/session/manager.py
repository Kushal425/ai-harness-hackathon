"""Session Manager (plan §3-§5 layer 2): owns mode, repo, conversation
history, and the active checkpoint/plan/result state. Both the plain REPL
and the TUI are thin views over this — slash-command behaviour must be
identical in either, so it lives here exactly once.

# PLAN-DECISION: /act does not replay a frozen plan object. `run_orchestrator`
# always plans fresh inside its plan_execute EXECUTE stage (there's no plan-
# injection hook, and adding one is more invasive than Phase 2's budget
# allows). /plan's stored Plan is for the user to *review* before committing;
# /act re-runs understand+plan+execute against the same stored goal text.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from raven.config import RavenConfig
from raven.core.executor import run_single_loop
from raven.core.orchestrator import OrchestratorResult, run_orchestrator
from raven.core.planner import Plan, make_plan
from raven.core.understand import Understanding, understand
from raven.llm.gateway import LLMGateway
from raven.llm.protocol import Message
from raven.recovery.checkpoints import CheckpointManager
from raven.repo.digest import build_digest
from raven.tools.registry import RunContext, build_default_registry

CHAT_SYSTEM_PROMPT = (
    "You are Raven, a conversational coding-agent harness. Answer plainly; "
    "say so if asked to edit code (chat mode is read-only — use /plan or /auto)."
)

# Mode auto-detect heuristics (plan §14.2): pasted text that looks like an
# issue report switches Chat straight into an autonomous run.
_STACK_TRACE_RE = re.compile(r"Traceback \(most recent call last\)|^\s*File \"", re.MULTILINE)
_ISSUE_PHRASES = ("steps to reproduce", "expected:", "actual:", "expected behavior", "actual behavior")
_GITHUB_ISSUE_RE = re.compile(r"github\.com/[\w.-]+/[\w.-]+/issues/\d+")


def looks_like_an_issue(text: str) -> bool:
    if len(text) > 150:
        return True
    if _STACK_TRACE_RE.search(text):
        return True
    lowered = text.lower()
    if any(phrase in lowered for phrase in _ISSUE_PHRASES):
        return True
    if _GITHUB_ISSUE_RE.search(text):
        return True
    return False


@dataclass
class SessionState:
    mode: str = "chat"  # chat | plan | act | autonomous | review
    repo_root: Path = field(default_factory=lambda: Path("."))
    goal: str | None = None
    understanding: Understanding | None = None
    plan: Plan | None = None
    last_result: OrchestratorResult | None = None


HELP_TEXT = """\
/help              show this list
/plan <task>       plan a task and wait for approval (does not execute)
/act               execute the last planned task
/auto <task>       plan and execute without stopping
/review            critique the current diff
/diff              show the current changes
/undo              revert the last run's checkpoints
/checkpoints       list files touched by the last run
/evidence          show evidence from the last run
/memory            show the repo digest summary
/budget            show token/call usage
/config            show the active configuration
/model             show the model in use
/repo <path>       switch repository
/chat              return to chat mode
/clear             clear the conversation
/exit              quit"""


class SessionManager:
    def __init__(self, config: RavenConfig, gateway: LLMGateway, repo_root: Path, approve_fn=None):
        self.config = config
        self.gateway = gateway
        self.state = SessionState(repo_root=Path(repo_root).resolve())
        self.history: list[Message] = [Message(role="system", content=CHAT_SYSTEM_PROMPT)]
        # None for every non-interactive caller (plain REPL, autonomous CLI,
        # eval harness) — only the TUI supplies a real approval prompt.
        self.approve_fn = approve_fn

    # -- slash command dispatch -------------------------------------------------

    def handle_input(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        if text.startswith("/"):
            return self._dispatch_command(text)
        return self._handle_chat(text)

    def _dispatch_command(self, text: str) -> str:
        parts = text.split(maxsplit=1)
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""
        handler = {
            "/help": self._cmd_help,
            "/plan": self._cmd_plan,
            "/act": self._cmd_act,
            "/auto": self._cmd_auto,
            "/review": self._cmd_review,
            "/diff": self._cmd_diff,
            "/undo": self._cmd_undo,
            "/checkpoints": self._cmd_checkpoints,
            "/evidence": self._cmd_evidence,
            "/memory": self._cmd_memory,
            "/budget": self._cmd_budget,
            "/config": self._cmd_config,
            "/model": self._cmd_model,
            "/repo": self._cmd_repo,
            "/chat": self._cmd_chat,
            "/clear": self._cmd_clear,
        }.get(cmd)
        if handler is None:
            return f"unknown command: {cmd} (try /help)"
        return handler(arg)

    def _cmd_help(self, _arg: str) -> str:
        return HELP_TEXT

    def _cmd_plan(self, arg: str) -> str:
        if not arg:
            return "usage: /plan <task description>"
        digest_summary = build_digest(self.state.repo_root).summary()
        understanding = understand(self.gateway, arg)
        plan = make_plan(self.gateway, understanding, digest_summary)
        self.state.goal = arg
        self.state.understanding = understanding
        self.state.plan = plan
        self.state.mode = "plan"
        return f"[plan mode] {understanding.task_type}: {understanding.summary}\n\n{plan.as_text()}\n\nRun /act to execute."

    def _cmd_act(self, _arg: str) -> str:
        if self.state.goal is None:
            return "no plan yet — run /plan <task> first"
        checkpoints = CheckpointManager(self.state.repo_root)
        # Reuse the understanding/plan /plan already showed the user (and
        # they may have reviewed/edited) instead of silently recomputing a
        # new one — what you approved is what runs.
        result = run_orchestrator(
            self.gateway, self.state.repo_root, self.state.goal,
            mode="act", strategy="plan_execute", checkpoints=checkpoints,
            approve_fn=self.approve_fn,
            understanding=self.state.understanding, plan=self.state.plan,
        )
        self.state.last_result = result
        self.state.mode = "act"
        return self._format_result(result)

    def _cmd_auto(self, arg: str) -> str:
        if not arg:
            return "usage: /auto <task description>"
        return self._run_autonomous(arg)

    def _run_autonomous(self, goal: str) -> str:
        strategy = self.config.raw.get("executor", {}).get("strategy", "single_loop")
        checkpoints = CheckpointManager(self.state.repo_root)
        result = run_orchestrator(
            self.gateway, self.state.repo_root, goal,
            mode="autonomous", strategy=strategy, checkpoints=checkpoints,
            approve_fn=self.approve_fn,  # inert in autonomous mode — policy.py never asks there
        )
        self.state.goal = goal
        self.state.last_result = result
        self.state.mode = "autonomous"
        return self._format_result(result)

    def _format_result(self, result: OrchestratorResult) -> str:
        lines = [
            f"run {result.run_id}: {'RESOLVED' if result.accepted else 'UNRESOLVED'}",
            f"reason: {result.reason}",
        ]
        if result.evidence:
            lines.append(f"evidence: {result.evidence}")
        lines.append(f"report: {result.report_path}")
        return "\n".join(lines)

    def _cmd_review(self, _arg: str) -> str:
        # Replaced once the Reviewer sub-agent exists (Task #16). Until then,
        # a plain diff is the best we can offer.
        try:
            from raven.agents.reviewer import review_diff
        except ImportError:
            return self._cmd_diff("") or "no diff to review"
        if self.state.last_result is None:
            return "no run yet to review"
        return review_diff(self.gateway, self.state.repo_root, self.state.last_result.evidence)

    def _cmd_diff(self, _arg: str) -> str:
        # is_repo_toplevel guards against a git repo nested inside a larger
        # one silently reporting the *outer* repo's diff (bit us once
        # already, behind the Reviewer sub-agent — see git_tool.py).
        from raven.tools.git_tool import is_repo_toplevel
        if not is_repo_toplevel(self.state.repo_root):
            return "(not a git repository)"
        proc = subprocess.run(
            ["git", "-C", str(self.state.repo_root), "diff"], capture_output=True, text=True
        )
        if proc.returncode != 0:
            return "(not a git repository)"
        return proc.stdout.strip() or "(no changes)"

    def _cmd_undo(self, _arg: str) -> str:
        if self.state.last_result is None or not self.state.last_result.checkpoints.has_changes():
            return "nothing to undo"
        restored = self.state.last_result.checkpoints.restore_to_clean()
        return "reverted: " + ", ".join(restored)

    def _cmd_checkpoints(self, _arg: str) -> str:
        if self.state.last_result is None:
            return "(no checkpoints this session)"
        touched = self.state.last_result.checkpoints.touched_paths
        return "\n".join(touched) if touched else "(no files touched)"

    def _cmd_evidence(self, _arg: str) -> str:
        if self.state.last_result is None:
            return "no run yet"
        return str(self.state.last_result.evidence)

    def _cmd_memory(self, _arg: str) -> str:
        return build_digest(self.state.repo_root).summary()

    def _cmd_budget(self, _arg: str) -> str:
        stats = self.gateway.stats
        return (
            f"calls: {stats.calls}  retries: {stats.retries}  "
            f"tokens: {stats.total_tokens} (prompt {stats.prompt_tokens} / completion {stats.completion_tokens})"
        )

    def _cmd_config(self, _arg: str) -> str:
        return (
            f"model: {self.config.llm.model}\nbase_url: {self.config.llm.base_url}\n"
            f"strategy: {self.config.raw.get('executor', {}).get('strategy')}\n"
            f"repo: {self.state.repo_root}"
        )

    def _cmd_model(self, _arg: str) -> str:
        return self.config.llm.model

    def _cmd_repo(self, arg: str) -> str:
        if not arg:
            return f"current repo: {self.state.repo_root}"
        candidate = Path(arg).expanduser().resolve()
        if not candidate.exists():
            return f"no such path: {arg}"
        self.state.repo_root = candidate
        return f"switched repo to {candidate}"

    def _cmd_chat(self, _arg: str) -> str:
        self.state.mode = "chat"
        return "back to chat mode"

    def _cmd_clear(self, _arg: str) -> str:
        self.history = [Message(role="system", content=CHAT_SYSTEM_PROMPT)]
        return "conversation cleared"

    # -- free-text handling -------------------------------------------------

    def _handle_chat(self, text: str) -> str:
        if self.state.mode == "autonomous":
            return self._run_autonomous(text)
        if self.state.mode == "chat" and looks_like_an_issue(text):
            note = "(this looks like an issue report — switching to autonomous mode. /chat to go back)\n\n"
            return note + self._run_autonomous(text)
        return self._chat_reply(text)

    def _chat_reply(self, text: str) -> str:
        try:
            registry = build_default_registry()
            ctx = RunContext(repo_root=self.state.repo_root, mode="chat")
            checkpoints = CheckpointManager(self.state.repo_root)
            digest_summary = build_digest(self.state.repo_root).summary()
            result = run_single_loop(
                self.gateway, registry, ctx, checkpoints, goal=text,
                digest_summary=digest_summary, max_iterations=6,
            )
            if result.completed and result.summary:
                return result.summary
        except Exception:
            pass  # fall through to a plain reply — chat must never hard-fail

        self.history.append(Message(role="user", content=text))
        try:
            completion = self.gateway.complete(self.history)
        except Exception as exc:
            return f"[error] {exc}"
        self.history.append(Message(role="assistant", content=completion.text))
        return completion.text
