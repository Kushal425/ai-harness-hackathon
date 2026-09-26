"""The single_loop executor strategy (plan §6.2): one agent loop with all
tools and a governed, freshly-assembled context each turn. This is the
baseline strategy and the fallback if plan_execute (Step 5) doesn't clearly
help on the prescribed model."""

from __future__ import annotations

from dataclasses import dataclass, field

from raven.agents.debugger import run_debugger
from raven.context.decay import HistoryEntry
from raven.context.engine import ContextEngine, ContextState
from raven.core import interrupt
from raven.core.protocol import FORMAT_REMINDER, ActionParseError, parse_action
from raven.llm.gateway import LLMGateway
from raven.recovery.checkpoints import CheckpointManager
from raven.recovery.handlers import RecoveryState
from raven.tools.registry import RunContext, ToolRegistry


@dataclass
class ExecutorResult:
    completed: bool  # model called done()
    summary: str
    iterations: int
    tool_calls: int
    touched_paths: list[str] = field(default_factory=list)
    aborted_reason: str | None = None
    # The most recent raw model completion, kept even on abort/parse-failure
    # so a caller (e.g. chat mode) can fall back to the model's own words
    # instead of firing a second, context-free call. See raven/session/
    # manager.py's _chat_reply.
    last_raw_text: str = ""


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
    stall_turns: int = 4,
    identical_failures_for_debugger: int = 3,
    answer_mode: bool = False,
) -> ExecutorResult:
    ctx.checkpoints = checkpoints
    context_engine = ContextEngine(tool_docs=registry.docs(), half_life=half_life)
    history: list[HistoryEntry] = []
    state = ContextState(
        task_card=goal, digest_summary=digest_summary, plan_text=plan_text,
        history=history, answer_mode=answer_mode,
    )
    last_raw_text = ""
    recovery = RecoveryState(
        stall_turns=stall_turns, identical_failures_for_debugger=identical_failures_for_debugger
    )

    consecutive_parse_failures = 0
    tool_calls = 0

    try:
        for turn in range(1, max_iterations + 1):
            if interrupt.is_interrupted():
                checkpoints.restore_to_clean()
                return ExecutorResult(
                    completed=False, summary="", iterations=turn, tool_calls=tool_calls,
                    aborted_reason="interrupted by user (Ctrl-C) — tree restored",
                    last_raw_text=last_raw_text,
                )

            messages = context_engine.assemble(state, turn)

            try:
                completion = gateway.complete(messages)
            except Exception as exc:
                checkpoints.restore_to_clean()
                return ExecutorResult(
                    completed=False, summary="", iterations=turn, tool_calls=tool_calls,
                    aborted_reason=f"LLM gateway error: {exc}", last_raw_text=last_raw_text,
                )
            last_raw_text = completion.text

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
                        last_raw_text=last_raw_text,
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

            repeat_nudge = recovery.check_repeated_action(action.tool, action.args, turn)
            if repeat_nudge is not None:
                history.append(HistoryEntry(turn=turn, role="result", text=f"[recovery] {repeat_nudge}"))
                stall_nudge = recovery.record_progress(made_progress=False)
                if stall_nudge:
                    history.append(HistoryEntry(turn=turn, role="result", text=f"[recovery] {stall_nudge}"))
                continue

            tool_result = registry.dispatch(action.tool, action.args, ctx)
            tool_calls += 1
            recovery.record_action_result(action.tool, action.args, turn, tool_result.output)
            history.append(
                HistoryEntry(
                    turn=turn,
                    role="result",
                    text=("OK: " if tool_result.ok else "ERROR: ") + tool_result.output,
                    path=action.args.get("path"),
                )
            )

            made_progress = False
            path = action.args.get("path")

            if action.tool == "edit":
                if tool_result.ok:
                    recovery.record_edit_success(path)
                    made_progress = True
                elif path and recovery.record_edit_failure(path):
                    fresh = registry.dispatch("read", {"path": path}, ctx)
                    history.append(HistoryEntry(
                        turn=turn, role="result", path=path,
                        text=f"[recovery] 2 consecutive failed edits on {path}; forcing a fresh read:\n{fresh.output}",
                    ))
            elif action.tool == "create" and tool_result.ok:
                made_progress = True
            elif action.tool == "tests":
                made_progress = True
                should_debug, signature = recovery.record_test_result(tool_result.output, tool_result.ok)
                if should_debug:
                    checkpoints.restore_to_clean()
                    nudge = run_debugger(gateway, registry, ctx, digest_summary, signature)
                    history.append(HistoryEntry(
                        turn=turn, role="result",
                        text=f"[recovery] stuck on repeated failure; rolled back this step's edits. {nudge}",
                    ))

            stall_nudge = recovery.record_progress(made_progress)
            if stall_nudge:
                history.append(HistoryEntry(turn=turn, role="result", text=f"[recovery] {stall_nudge}"))

        return ExecutorResult(
            completed=False, summary="", iterations=max_iterations, tool_calls=tool_calls,
            touched_paths=checkpoints.touched_paths,
            aborted_reason="budget exhausted: max_iterations reached without calling done",
            last_raw_text=last_raw_text,
        )
    except Exception as exc:  # never let the loop crash the run (plan §0.4)
        checkpoints.restore_to_clean()
        return ExecutorResult(
            completed=False, summary="", iterations=0, tool_calls=tool_calls,
            aborted_reason=f"unexpected executor error (tree restored): {exc}",
            last_raw_text=last_raw_text,
        )
