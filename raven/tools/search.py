"""Plain-Python text search (plan §8.2). No ripgrep dependency required."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from raven.repo.files import SKIP_DIRS, list_repo_files  # noqa: F401  (SKIP_DIRS re-exported)
from raven.tools.registry import RunContext, Tool, ToolResult


def _iter_files(repo_root: Path, glob: str | None):
    for rel in list_repo_files(repo_root):
        if glob and not fnmatch.fnmatch(rel, glob):
            continue
        yield repo_root / rel


def search(ctx: RunContext, pattern: str, glob: str | None = None, regex: bool = False) -> ToolResult:
    repo_root = ctx.repo_root.resolve()
    matcher = re.compile(pattern) if regex else None
    hits_by_file: dict[str, list[str]] = {}

    for path in _iter_files(repo_root, glob):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        rel = str(path.relative_to(repo_root))
        for lineno, line in enumerate(text.splitlines(), start=1):
            matched = matcher.search(line) if matcher else (pattern in line)
            if matched:
                hits_by_file.setdefault(rel, []).append(f"{lineno}: {line.strip()}")

    if not hits_by_file:
        return ToolResult(ok=True, output=f"no matches for {pattern!r}", data={"hits": 0})

    ranked = sorted(hits_by_file.items(), key=lambda kv: -len(kv[1]))
    out_lines = []
    total_hits = 0
    for rel, hits in ranked:
        out_lines.append(f"{rel} ({len(hits)} hits)")
        out_lines.extend(f"  {h}" for h in hits[:10])
        total_hits += len(hits)
    return ToolResult(ok=True, output="\n".join(out_lines), data={"hits": total_hits, "files": len(ranked)})


SEARCH_TOOL = Tool(
    name="search",
    description="Search file contents for a pattern (substring by default, regex if regex=true). "
    "Optionally filter by glob. Results grouped by file, ranked by hit count.",
    params={"pattern": "str", "glob": "str?", "regex": "bool?"},
    permission="read",
    fn=search,
)
