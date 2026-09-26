"""The single_loop executor strategy (plan §6.2): one agent loop with all
tools and a governed, freshly-assembled context each turn. This is the
baseline strategy and the fallback if plan_execute (Step 5) doesn't clearly
help on the prescribed model."""

from __future__ import annotations

from dataclasses import dataclass, field

from raven.context.decay import HistoryEntry
from raven.context.engine import ContextEngine, ContextState
from raven.core.protocol import FORMAT_REMINDER, ActionParseError, parse_action
from raven.llm.gateway import LLMGateway
from raven.recovery.checkpoints import CheckpointManager
from raven.tools.registry import RunContext, ToolRegistry


@dataclass
class ExecutorResult:
    completed: bool  # model called done()
    summary: str
    iterations: int
    tool_calls: int
    touched_paths: list[str] = field(default_factory=list)
    aborted_reason: str | None = None


def run_single_loop(
    gateway: LLMGateway,
    registry: ToolRegistry,
    ctx: RunContext,
    checkpoints: CheckpointManager,
    goal: str,
    digest_summary: str = "",
    plan_text: str = "",
    max_iterations: int = 15,
    half_life: int = 3,
) -> ExecutorResult:
    ctx.checkpoints = checkpoints
    context_engine = ContextEngine(tool_docs=registry.docs(), half_life=half_life)
    history: list[HistoryEntry] = []
    state = ContextState(task_card=goal, digest_summary=digest_summary, plan_text=plan_text, history=history)

    consecutive_parse_failures = 0
    tool_calls = 0

    try:
        for turn in range(1, max_iterations + 1):
            messages = context_engine.assemble(state, turn)

            try:
                completion = gateway.complete(messages)
            except Exception as exc:
                checkpoints.restore_to_clean()
                return ExecutorResult(
                    completed=False, summary="", iterations=turn, tool_calls=tool_calls,
                    aborted_reason=f"LLM gateway error: {exc}",
                )

            try:
                action = parse_action(completion.text)
                consecutive_parse_failures = 0
            except ActionParseError as exc:
                consecutive_parse_failures += 1
                history.append(HistoryEntry(turn=turn, role="result", text=f"[parse error] {exc}\n{FORMAT_REMINDER}"))
                if consecutive_parse_failures >= 3:
                    checkpoints.restore_to_clean()
                    return ExecutorResult(
                        completed=False, summary="", iterations=turn, tool_calls=tool_calls,
                        aborted_reason="aborted: 3 consecutive malformed action blocks",
                    )
                continue

            history.append(HistoryEntry(turn=turn, role="action", text=f"{action.tool}({action.args})"))

            if action.tool == "done":
                return ExecutorResult(
                    completed=True,
                    summary=str(action.args.get("summary", "")),
                    iterations=turn,
                    tool_calls=tool_calls,
                    touched_paths=checkpoints.touched_paths,
                )

            tool_result = registry.dispatch(action.tool, action.args, ctx)
            tool_calls += 1
            history.append(
                HistoryEntry(
                    turn=turn,
                    role="result",
                    text=("OK: " if tool_result.ok else "ERROR: ") + tool_result.output,
                    path=action.args.get("path"),
                )
            )

        return ExecutorResult(
            completed=False, summary="", iterations=max_iterations, tool_calls=tool_calls,
            touched_paths=checkpoints.touched_paths,
            aborted_reason="budget exhausted: max_iterations reached without calling done",
        )
    except Exception as exc:  # never let the loop crash the run (plan §0.4)
        checkpoints.restore_to_clean()
        return ExecutorResult(
            completed=False, summary="", iterations=0, tool_calls=tool_calls,
            aborted_reason=f"unexpected executor error (tree restored): {exc}",
        )
