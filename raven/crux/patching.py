"""Candidate patches as immutable snapshots (the Coherence-Collapse fix: a
good edit is never overwritten by a later one -- every candidate is kept
whole and selected by evidence).

Edits are search/replace pairs applied deterministically in memory (exact,
then whitespace-normalised unique match), syntax-checked, and only ever
written to disk briefly while that candidate is being executed.
"""

from __future__ import annotations

import ast
import difflib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from raven.tools.edit import _find_normalized


@dataclass
class Edit:
    path: str
    search: str
    replace: str


@dataclass
class Candidate:
    id: str
    hypothesis: str
    edits: list[Edit]
    files: dict[str, str] = field(default_factory=dict)     # path -> new full text
    originals: dict[str, str] = field(default_factory=dict)  # path -> original text
    error: str | None = None                                 # why it couldn't be built
    status: str = "built"                                    # built | invalid | broke-tests | alive | ...
    notes: list[str] = field(default_factory=list)

    @property
    def diff(self) -> str:
        chunks = []
        for path, new in self.files.items():
            chunks.extend(difflib.unified_diff(
                self.originals[path].splitlines(keepends=True), new.splitlines(keepends=True),
                fromfile=f"a/{path}", tofile=f"b/{path}", n=1,
            ))
        return "".join(chunks)

    @property
    def size(self) -> int:
        return sum(1 for l in self.diff.splitlines() if l[:1] in "+-" and l[:3] not in ("+++", "---"))

    def normalized(self) -> str:
        """Semantics-preserving normal form for voting (Agentless): parse +
        unparse each file, docstrings and formatting stripped."""
        out = []
        for path in sorted(self.files):
            out.append(f"{path}\n{_normalize_py(self.files[path])}" if path.endswith(".py") else self.files[path])
        return "\n".join(out)


def apply_edit(text: str, edit: Edit) -> tuple[str | None, str]:
    """(new_text, "") or (None, reason)."""
    if not edit.search:
        return None, "empty search text"
    count = text.count(edit.search)
    if count == 1:
        return text.replace(edit.search, edit.replace, 1), ""
    if count > 1:
        return None, f"search text matches {count} places in {edit.path}; include more surrounding lines"
    offset = _find_normalized(text, edit.search)
    if offset is None:
        offset = _find_similar(text, edit.search)
    if offset is None:
        return None, f"search text not found in {edit.path}" + _closest_hint(text, edit.search)
    lines = text.splitlines(keepends=True)
    running, start = 0, 0
    for i, line in enumerate(lines):
        if running == offset:
            start = i
            break
        running += len(line)
    n = len(edit.search.splitlines()) or 1
    replace = edit.replace if edit.replace.endswith("\n") or not edit.replace else edit.replace + "\n"
    return "".join(lines[:start]) + replace + "".join(lines[start + n:]), ""


def build_candidate(repo_root: Path, cid: str, hypothesis: str, edits: list[Edit]) -> Candidate:
    cand = Candidate(id=cid, hypothesis=hypothesis, edits=edits)
    repo_root = Path(repo_root).resolve()
    if not edits:
        cand.error, cand.status = "no edits proposed", "invalid"
        return cand
    for edit in edits:
        target = (repo_root / edit.path).resolve()
        if repo_root not in target.parents:
            cand.error, cand.status = f"path outside repo: {edit.path}", "invalid"
            return cand
        if edit.path not in cand.files:
            if not target.is_file():
                cand.error, cand.status = f"no such file: {edit.path}", "invalid"
                return cand
            cand.originals[edit.path] = target.read_text(errors="replace")
            cand.files[edit.path] = cand.originals[edit.path]
        new, err = apply_edit(cand.files[edit.path], edit)
        if new is None:
            cand.error, cand.status = err, "invalid"
            return cand
        cand.files[edit.path] = new
    for path, text in cand.files.items():
        if path.endswith(".py"):
            try:
                compile(text, path, "exec")
            except SyntaxError as exc:
                cand.error, cand.status = f"syntax error in {path}:{exc.lineno}: {exc.msg}", "invalid"
                return cand
    if all(cand.files[p] == cand.originals[p] for p in cand.files):
        cand.error, cand.status = "edits change nothing", "invalid"
    return cand


@contextmanager
def applied(repo_root: Path, cand: Candidate | None):
    """Temporarily write `cand`'s files to disk; always restores."""
    repo_root = Path(repo_root)
    if cand is None:
        yield
        return
    try:
        for path, text in cand.files.items():
            (repo_root / path).write_text(text)
        yield
    finally:
        for path, text in cand.originals.items():
            (repo_root / path).write_text(text)


def _normalize_py(src: str) -> str:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _find_similar(text: str, search: str, threshold: float = 0.9) -> int | None:
    """Last resort for small models that copy code *almost* exactly: the
    single most similar block with the same number of lines, if it is at
    least `threshold` similar and clearly better than the runner-up."""
    lines = text.splitlines(keepends=True)
    n = len(search.splitlines()) or 1
    target = " ".join(search.split())
    scored = []
    for i in range(len(lines) - n + 1):
        window = " ".join("".join(lines[i:i + n]).split())
        scored.append((difflib.SequenceMatcher(None, window, target).ratio(), i))
    if not scored:
        return None
    scored.sort(reverse=True)
    best, i = scored[0]
    if best < threshold or (len(scored) > 1 and scored[1][0] > best - 0.05):
        return None
    return sum(len(l) for l in lines[:i])


def _closest_hint(text: str, search: str) -> str:
    """Show the model the closest real lines, so its one repair can copy them."""
    first = next((l.strip() for l in search.splitlines() if l.strip()), "")
    matches = difflib.get_close_matches(first, [l.strip() for l in text.splitlines()], n=1, cutoff=0.5)
    return f"; the closest existing line is: {matches[0]!r}" if matches else ""
