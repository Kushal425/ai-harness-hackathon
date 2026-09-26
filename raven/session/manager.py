"""Session Manager (plan §3-§5 layer 2): owns mode, repo, conversation
history, and the active checkpoint/plan/result state. Both the plain REPL
and the TUI are thin views over this — slash-command behaviour must be
identical in either, so it lives here exactly once.

/act reuses the exact Understanding/Plan objects /plan already computed and
showed the user (passed through to run_orchestrator) rather than
recomputing them — what you approved is what runs. Only a REPLAN (the
Judge rejects the first pass) calls make_plan() again, since the approved
plan demonstrably didn't work. See raven/core/orchestrator.py.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from raven.config import RavenConfig
from raven.core.executor import run_single_loop
from raven.core.judge import outcome_label
from raven.core.orchestrator import OrchestratorResult, run_orchestrator
from raven.core.planner import Plan, make_plan
from raven.core.understand import Understanding, understand
from raven.llm.gateway import LLMGateway
from raven.llm.protocol import Message
from raven.prompts import get_prompt
from raven.recovery.checkpoints import CheckpointManager
from raven.repo.digest import build_digest
from raven.github import parse_issue_ref
from raven.intake import clone_repo, expand_issue_refs, github_issue_task, github_workspace, looks_like_git_url
from raven.repo.files import exclude_raven_dir
from raven.workspace import export_patch
from raven.tools.registry import RunContext, build_default_registry

# Text lives in prompts/base.yaml (plan §12.3).
CHAT_SYSTEM_PROMPT = get_prompt("chat_system")

# Mode auto-detect heuristics (plan §14.2): pasted text that looks like an
# issue report switches Chat straight into an autonomous run.
_STACK_TRACE_RE = re.compile(r"Traceback \(most recent call last\)|^\s*File \"", re.MULTILINE)
_ISSUE_PHRASES = ("steps to reproduce", "expected:", "actual:", "expected behavior", "actual behavior")
_GITHUB_ISSUE_RE = re.compile(r"github\.com/[\w.-]+/[\w.-]+/issues/\d+")


_GH_SHORT_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


def _is_github_checkout(repo_root: Path, owner: str, name: str) -> bool:
    """Is `repo_root` a clone of github.com/owner/name (by its origin URL)?"""
    try:
        url = subprocess.run(["git", "-C", str(repo_root), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=5).stdout.strip().lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return url.rstrip("/").removesuffix(".git").endswith(f"{owner}/{name}".lower())


_SMALL_TALK_RE = re.compile(
    r"^\s*(hi+|hello+|hey+|yo|hola|namaste|good (morning|afternoon|evening|night)|thanks?( you)?|thank you|"
    r"thx|ty|ok(ay)?|cool|nice|great|bye|goodbye|see you|who are you|what are you|how are you|"
    r"what('?s| is) your name|sup|what'?s up)[\s!.?,]*(raven)?[\s!.?]*$",
    re.IGNORECASE,
)


def is_small_talk(text: str) -> bool:
    return bool(_SMALL_TALK_RE.match(text))


def _is_tool_call(text: str) -> bool:
    from raven.core.protocol import ActionParseError, parse_action

    try:
        parse_action(text)
        return True
    except ActionParseError:
        return False


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
/lessons           show lessons learned from past runs on this repo
/budget            show token/call usage
/config            show the active configuration
/model             show the model in use
/repo <path|url>   switch repository (a GitHub repo gets an isolated worktree)
/gh                GitHub: login · status · repos [filter] · use <repo> · branches ·
                   issues [filter] · issue <#|url> (runs the agent on it) · logout
/chat              return to chat mode
/clear             clear the conversation
/exit              quit"""


class SessionManager:
    def __init__(self, config: RavenConfig, gateway: LLMGateway, repo_root: Path, approve_fn=None, on_event=None):
        self.config = config
        self.gateway = gateway
        self.state = SessionState(repo_root=Path(repo_root).resolve())
        exclude_raven_dir(self.state.repo_root)
        self.history: list[Message] = [Message(role="system", content=CHAT_SYSTEM_PROMPT)]
        # None for every non-interactive caller (plain REPL, autonomous CLI,
        # eval harness) — only the TUI supplies a real approval prompt.
        self.approve_fn = approve_fn
        # None for callers that don't want a live tool-call stream (eval
        # harness, tests). Both the TUI and plain REPL/piped paths set this
        # once at construction and it stays constant for the session.
        self.on_event = on_event
        # GitHub (lazy): client, cached listings, and the selected (owner, name, branch)
        self._gh = None
        self._gh_repos = None
        self._gh_listing: list[str] = []
        self._gh_repo = None
        self._gh_issues = None

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
            "/lessons": self._cmd_lessons,
            "/budget": self._cmd_budget,
            "/config": self._cmd_config,
            "/model": self._cmd_model,
            "/repo": self._cmd_repo,
            "/gh": self._cmd_gh,
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
            on_event=self.on_event, **self.config.run_kwargs(),
        )
        self.state.last_result = result
        self.state.mode = "act"
        return self._format_result(result)

    def _cmd_auto(self, arg: str) -> str:
        if not arg:
            return "usage: /auto <task description>"
        return self._run_autonomous(arg)

    def _run_autonomous(self, goal: str) -> str:
        ref = parse_issue_ref(goal)
        if ref and not _is_github_checkout(self.state.repo_root, ref[0], ref[1]):
            # an issue from another repository: fetch that repo into an isolated worktree
            try:
                self._say(f"fetching {ref[0]}/{ref[1]}#{ref[2]} into an isolated workspace...")
                path, task = github_issue_task(*ref)
            except RuntimeError as exc:
                return f"GitHub: {exc}"
            self._switch_repo(path)
            goal = task + ("\n\n" + goal if goal.strip() != goal.strip().split()[0] else "")
        else:
            goal = expand_issue_refs(goal)  # a pasted GitHub issue URL -> its full text
        strategy = self.config.raw.get("executor", {}).get("strategy", "crux")
        checkpoints = CheckpointManager(self.state.repo_root)
        result = run_orchestrator(
            self.gateway, self.state.repo_root, goal,
            mode="autonomous", strategy=strategy, checkpoints=checkpoints,
            approve_fn=self.approve_fn,  # inert in autonomous mode — policy.py never asks there
            on_event=self.on_event, **self.config.run_kwargs(),
        )
        self.state.goal = goal
        self.state.last_result = result
        # One-shot: back to chat once the run is reported, so the next
        # message ("hi", a question) is a conversation, not another task.
        # A new issue still auto-detects, or use /auto.
        self.state.mode = "chat"
        text = self._format_result(result)
        try:
            patch = export_patch(self.state.repo_root, result.report_path)
        except Exception:
            patch = None
        if patch:
            text += f"\nworktree: {self.state.repo_root}\npatch: {patch} (nothing was pushed)"
        return text

    def _format_result(self, result: OrchestratorResult) -> str:
        lines = [
            f"run {result.run_id}: {outcome_label(result.accepted, result.verified)}",
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

    def _cmd_lessons(self, _arg: str) -> str:
        from raven.memory.lessons import GLOBAL_LESSONS_PATH, list_lessons, repo_lessons_path

        repo = list_lessons(repo_lessons_path(self.state.repo_root))
        glob = list_lessons(GLOBAL_LESSONS_PATH)
        if not repo and not glob:
            return "(no lessons learned yet on this repo)"
        lines = []
        if repo:
            lines.append("repo-scope:")
            lines += [f"  - {l.get('insight', '')} (x{l.get('confirmations', 1)})" for l in repo]
        if glob:
            lines.append("global-scope:")
            lines += [f"  - {l.get('insight', '')} (x{l.get('confirmations', 1)})" for l in glob]
        return "\n".join(lines)

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
        if looks_like_git_url(arg) or (_GH_SHORT_RE.match(arg) and not Path(arg).expanduser().exists()):
            try:
                candidate = clone_repo(arg)
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                return f"could not clone {arg}: {exc}"
        else:
            candidate = Path(arg).expanduser().resolve()
            if not candidate.is_dir():
                return f"no such directory: {arg}"
        self._switch_repo(candidate)
        return f"switched repo to {candidate}"

    def _switch_repo(self, path: Path) -> None:
        self.state.repo_root = Path(path).resolve()
        exclude_raven_dir(self.state.repo_root)

    def _say(self, text: str) -> None:
        """Progress notices for long GitHub operations (shown immediately)."""
        if self.on_event:
            try:
                self.on_event("notice", {"text": text})
                return
            except Exception:
                pass
        print(text)

    # -- GitHub ----------------------------------------------------------------

    def _gh_client(self):
        from raven.github import GitHubClient

        if self._gh is None:
            self._gh = GitHubClient()
        return self._gh

    def _cmd_gh(self, arg: str) -> str:
        from raven import github as gh

        sub, _, rest = arg.strip().partition(" ")
        rest = rest.strip()
        try:
            if sub in ("", "help"):
                return ("GitHub: /gh login | status | repos [filter] | use <#|owner/name|url> [branch] | "
                        "branches | issues [filter] | issue <#|url> [branch] | logout")
            if sub == "login":
                token = gh.device_login(os.environ.get("RAVEN_GITHUB_CLIENT_ID", ""),
                                        os.environ.get("RAVEN_GITHUB_SCOPES", gh.DEFAULT_SCOPES), on_code=self._say)
                login = gh.GitHubClient(token).viewer()
                gh.save_token(token, login)
                self._gh = None
                return f"connected to GitHub as @{login} (token saved to {gh.token_path()}, mode 0600)"
            if sub == "logout":
                removed = gh.forget_token()
                self._gh = None
                note = "" if not os.environ.get("GITHUB_TOKEN") else " (GITHUB_TOKEN is still set in the environment)"
                return ("signed out" if removed else "no saved login") + note
            if sub == "status":
                token, source = gh.resolve_token()
                if not token:
                    return "not connected: /gh login, export GITHUB_TOKEN, or `gh auth login`"
                return f"connected as @{self._gh_client().viewer()} via {source}" + (
                    f"; repo {self._gh_repo[0]}/{self._gh_repo[1]} ({self._gh_repo[2]})" if self._gh_repo else "")
            if sub == "repos":
                if self._gh_repos is None:
                    self._gh_repos = self._gh_client().repos()
                needle = rest.lower()
                shown = [r for r in self._gh_repos if needle in f"{r.full_name} {r.description} {r.language}".lower()]
                if not shown:
                    return f"no repositories match {rest!r}"
                lines = [f"{i:>3}. {r.line()}" for i, r in enumerate(shown[:40], 1)]
                self._gh_listing = [r.full_name for r in shown[:40]]
                more = f"\n... {len(shown) - 40} more; narrow with /gh repos <filter>" if len(shown) > 40 else ""
                return "\n".join(lines) + more + "\n\n/gh use <number or owner/name> to work on one"
            if sub == "use":
                target, _, branch = rest.partition(" ")
                if target.isdigit() and self._gh_listing and 1 <= int(target) <= len(self._gh_listing):
                    target = self._gh_listing[int(target) - 1]
                owner, name = gh.parse_repo(target)
                self._say(f"fetching {owner}/{name} into an isolated workspace...")
                path, branch = github_workspace(owner, name, branch.strip() or None, label="session",
                                                client=self._gh_or_anonymous())
                self._switch_repo(path)
                self._gh_repo, self._gh_issues = (owner, name, branch), None
                return (f"working on {owner}/{name} @ {branch} in an isolated worktree:\n  {path}\n"
                        "/gh issues to pick an issue, or just ask questions / /auto a task")
            if sub == "branches":
                if not self._gh_repo:
                    return "no GitHub repository selected: /gh use <repo>"
                from raven.workspace import remote_branches
                base = self.state.repo_root.parent.parent / "base"
                return "\n".join(remote_branches(base)) or "(no branches)"
            if sub == "issues":
                if not self._gh_repo:
                    return "no GitHub repository selected: /gh use <repo>"
                if self._gh_issues is None:
                    self._gh_issues = self._gh_or_anonymous().issues(self._gh_repo[0], self._gh_repo[1])
                needle = rest.lower()
                shown = [i for i in self._gh_issues if needle in f"{i.title} {' '.join(i.labels)}".lower()]
                if not shown:
                    return "no open issues" + (f" match {rest!r}" if rest else "")
                return "\n".join(f"#{i.number:<5} {i.title[:90]}" + (f"  [{', '.join(i.labels)}]" if i.labels else "")
                                 for i in shown[:40]) + "\n\n/gh issue <number> to run the agent on one"
            if sub == "issue":
                target, _, branch = rest.partition(" ")
                ref = gh.parse_issue_ref(target)
                if ref is None:
                    if not (target.lstrip("#").isdigit() and self._gh_repo):
                        return "usage: /gh issue <number> (after /gh use <repo>) or /gh issue <issue URL>"
                    ref = (self._gh_repo[0], self._gh_repo[1], int(target.lstrip("#")))
                    branch = branch or self._gh_repo[2]
                self._say(f"fetching {ref[0]}/{ref[1]}#{ref[2]} into a fresh worktree...")
                path, task = github_issue_task(*ref, branch=branch.strip() or None, client=self._gh_or_anonymous())
                self._switch_repo(path)
                return self._run_autonomous(task)
            return f"unknown /gh command: {sub} (try /gh)"
        except gh.GitHubAuthError as exc:
            self._gh = None
            return f"GitHub sign-in needed: {exc}"
        except (gh.GitHubError, RuntimeError, ValueError) as exc:
            return f"GitHub: {exc}"

    def _gh_or_anonymous(self):
        from raven.github import GitHubAuthError, GitHubClient

        try:
            return self._gh_client()
        except GitHubAuthError:
            return GitHubClient(allow_anonymous=True)

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

    def _repo_context(self) -> str:
        root = self.state.repo_root
        try:
            digest = build_digest(root).summary()
        except Exception:
            digest = "(digest unavailable)"
        return f"repository: {root.name}  ({root})\n{digest}"

    def _chat_reply(self, text: str) -> str:
        if is_small_talk(text):
            # "hello", "thanks", "who are you": nothing to look up -- one
            # direct reply, no tool loop (small models otherwise go exploring)
            return self._plain_reply(text)
        try:
            registry = build_default_registry()
            ctx = RunContext(repo_root=self.state.repo_root, mode="chat")
            checkpoints = CheckpointManager(self.state.repo_root)
            digest_summary = self._repo_context()
            result = run_single_loop(
                self.gateway, registry, ctx, checkpoints, goal=text,
                digest_summary=digest_summary, max_iterations=6, answer_mode=True,
                on_event=self.on_event,
            )
            if result.completed and result.summary:
                return result.summary
            # The model didn't call done() (e.g. it just replied in plain
            # prose without an action block — common for casual questions).
            # Use its own raw words rather than firing a second, tool-blind
            # call that has no idea Raven can read the repo at all.
            if result.last_raw_text and not _is_tool_call(result.last_raw_text):
                return result.last_raw_text
            # Out of turns mid-exploration: never show a raw tool call as
            # the answer -- ask once more for a plain answer instead.
        except Exception:
            pass  # fall through to a plain reply — chat must never hard-fail
        return self._plain_reply(text)

    def _plain_reply(self, text: str) -> str:
        """One tool-free, repo-grounded chat call."""
        self.history.append(Message(role="user", content=text))
        # The fallback still knows which repo it's in -- without this, a
        # question like "what is this repo about?" gets "which repo?".
        grounding = Message(role="system", content=f"# Repository digest\n{self._repo_context()}")
        try:
            completion = self.gateway.complete([self.history[0], grounding] + self.history[1:])
        except Exception as exc:
            return f"[error] {exc}"
        self.history.append(Message(role="assistant", content=completion.text))
        return completion.text


    # -- completion data for the TUI (no network: cached listings only) ----

    def completions(self) -> dict:
        return {
            "commands": [line.split()[0] for line in HELP_TEXT.splitlines() if line.startswith("/")],
            "repos": [r.full_name for r in (self._gh_repos or [])],
            "issues": [f"{i.number}" for i in (self._gh_issues or [])],
        }
