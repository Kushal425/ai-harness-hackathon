"""AST-based Python symbol lookup: find definitions and outline a file
(plan §8.2, §8.3). Regex-based finders for other languages are Phase-2+."""

from __future__ import annotations

import ast
from pathlib import Path

from raven.tools.registry import RunContext, Tool, ToolResult
from raven.tools.search import SKIP_DIRS


def _iter_py_files(repo_root: Path):
    for path in repo_root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def _defs_in_file(path: Path) -> list[tuple[str, str, int, int]]:
    try:
        tree = ast.parse(path.read_text(errors="ignore"))
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            out.append(("function", node.name, node.lineno, end))
        elif isinstance(node, ast.ClassDef):
            end = getattr(node, "end_lineno", node.lineno)
            out.append(("class", node.name, node.lineno, end))
    return out


def symbols(ctx: RunContext, query: str) -> ToolResult:
    repo_root = ctx.repo_root.resolve()
    hits = []
    for path in _iter_py_files(repo_root):
        for kind, name, start, end in _defs_in_file(path):
            if query.lower() in name.lower():
                rel = path.relative_to(repo_root)
                hits.append(f"{kind} {name}  {rel}:{start}-{end}")
    if not hits:
        return ToolResult(ok=True, output=f"no symbols matching {query!r}")
    return ToolResult(ok=True, output="\n".join(hits), data={"count": len(hits)})


def outline(ctx: RunContext, path: str) -> ToolResult:
    resolved = (ctx.repo_root / path).resolve()
    if not resolved.exists():
        return ToolResult(ok=False, output=f"no such file: {path}")
    defs = _defs_in_file(resolved)
    if not defs:
        return ToolResult(ok=True, output="(no top-level classes/functions found)")
    lines = [f"{kind} {name}  lines {start}-{end}" for kind, name, start, end in defs]
    return ToolResult(ok=True, output="\n".join(lines))


SYMBOLS_TOOL = Tool(
    name="symbols",
    description="Find function/class definitions whose name contains the query string.",
    params={"query": "str"},
    permission="read",
    fn=symbols,
)

OUTLINE_TOOL = Tool(
    name="outline",
    description="List the classes and functions defined in a file with their line ranges.",
    params={"path": "str"},
    permission="read",
    fn=outline,
)
