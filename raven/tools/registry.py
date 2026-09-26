"""Tool registry: declaration, dispatch, policy enforcement, and result
shaping (plan §8.1, §8.4). Every tool is a plain function taking a
RunContext plus keyword args and returning a ToolResult."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from raven.tools.policy import PolicyDecision, check_policy


@dataclass
class ToolResult:
    ok: bool
    output: str
    data: dict = field(default_factory=dict)


@dataclass
class RunContext:
    """Shared state passed to every tool call. `checkpoints` is set by the
    executor (raven/recovery/checkpoints.py) before write tools run.
    `approve_fn` is None for every non-interactive caller (autonomous CLI,
    eval harness, plain chat) — only the TUI wires one up (plan §8.5)."""

    repo_root: Path
    mode: str = "act"  # chat | plan | act | autonomous
    checkpoints: Any = None
    run_dir: Path | None = None
    approve_fn: Any = None


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    permission: str  # "read" | "write" | "exec"
    fn: Callable[..., ToolResult]


def shape_text(text: str, max_lines: int = 200) -> str:
    """Truncates long output keeping head+tail, per plan §8.4."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    head = lines[: max_lines // 2]
    tail = lines[-max_lines // 2 :]
    omitted = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"... ({omitted} lines omitted) ..."] + tail)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def docs(self) -> str:
        lines = []
        for name in self.names():
            tool = self._tools[name]
            lines.append(f"- {tool.name}({', '.join(tool.params)}): {tool.description}")
        return "\n".join(lines)

    def dispatch(self, name: str, args: dict, ctx: RunContext) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(ok=False, output=f"unknown tool: {name}")

        decision = check_policy(ctx.mode, tool.name, tool.permission, args, approve_fn=ctx.approve_fn)
        if decision == PolicyDecision.DENY:
            return ToolResult(
                ok=False,
                output=f"denied by policy: tool={name} mode={ctx.mode} (see raven/tools/policy.py)",
            )

        if tool.permission == "write" and ctx.checkpoints is not None:
            path = args.get("path")
            if path:
                ctx.checkpoints.snapshot(path)

        try:
            result = tool.fn(ctx, **args)
        except TypeError as exc:
            return ToolResult(ok=False, output=f"bad arguments for {name}: {exc}")
        except Exception as exc:  # tools must never crash the executor loop
            return ToolResult(ok=False, output=f"tool error in {name}: {exc}")

        result.output = shape_text(result.output)
        return result


def build_default_registry() -> ToolRegistry:
    """Registers the Phase-1 tool set. Imported lazily to avoid a circular
    import (each tool module imports ToolResult from here)."""
    from raven.tools import edit as edit_tool
    from raven.tools import fs as fs_tool
    from raven.tools import git_tool
    from raven.tools import search as search_tool
    from raven.tools import shell as shell_tool
    from raven.tools import symbols as symbols_tool
    from raven.tools import test_runner

    registry = ToolRegistry()
    for tool in [
        fs_tool.READ_TOOL,
        fs_tool.CREATE_TOOL,
        search_tool.SEARCH_TOOL,
        symbols_tool.SYMBOLS_TOOL,
        symbols_tool.OUTLINE_TOOL,
        edit_tool.EDIT_TOOL,
        shell_tool.SHELL_TOOL,
        test_runner.TESTS_TOOL,
        git_tool.STATUS_TOOL,
        git_tool.DIFF_TOOL,
    ]:
        registry.register(tool)
    return registry
