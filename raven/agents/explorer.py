"""Explorer sub-agent (plan §7.3): read-only repository navigation. Given a
question like "where is X / how does Y work", runs a bounded read-only
loop and returns a short structured finding with file:line citations,
instead of the orchestrator's own Engineer loop spending its context
budget reading around blindly."""

from __future__ import annotations

from raven.core.executor import run_single_loop
from raven.recovery.checkpoints import CheckpointManager
from raven.tools.registry import RunContext, build_default_registry

EXPLORER_GOAL_PREFIX = (
    "You are Raven's Explorer sub-agent. Investigate the following question "
    "using only read-only tools (read, search, symbols, outline, git_status). "
    "When you have an answer, call done with a concise summary (aim for "
    "under 300 tokens) that cites specific file:line locations.\n\nQuestion: "
)

# Cheap proxy heuristic for "this plan step needs broad exploration" (plan
# §7.3, delegate_min_reads config): we can't know the actual read count a
# step will need in advance, so we use step-action wording/length as a
# stand-in. Keyword hits are the strong signal; step length is a weak
# tie-breaker for long, vaguely-worded steps.
_EXPLORATORY_KEYWORDS = ("understand", "find", "figure out", "investigate", "how does", "where is", "locate")


def should_delegate_to_explorer(step_action: str, delegate_min_reads: int = 4) -> bool:
    text = step_action.lower()
    if any(kw in text for kw in _EXPLORATORY_KEYWORDS):
        return True
    return len(step_action.split()) >= delegate_min_reads * 3


def explore(gateway, repo_root, question: str, digest_summary: str = "", max_iterations: int = 8) -> str:
    """Returns a short structured finding, or an honest "couldn't
    conclude" note. Never raises — a broken Explorer must not take down
    the run it's meant to help."""
    registry = build_default_registry()
    ctx = RunContext(repo_root=repo_root, mode="chat")  # chat mode: policy enforces read-only
    checkpoints = CheckpointManager(repo_root)
    try:
        result = run_single_loop(
            gateway, registry, ctx, checkpoints, goal=EXPLORER_GOAL_PREFIX + question,
            digest_summary=digest_summary, max_iterations=max_iterations,
        )
    except Exception as exc:
        return f"Explorer: error during exploration ({exc})"
    if result.completed and result.summary:
        return result.summary
    return f"Explorer: could not reach a conclusion ({result.aborted_reason or 'budget exhausted'})"
