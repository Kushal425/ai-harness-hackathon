"""Ranked repo map (deterministic; replaces exploratory tool calls).

Indexes every Python function/method (signature, docstring, names used,
line range) and scores it against the issue card: stack-trace frames >
exact name matches > mentioned paths > identifiers used in the body, with a
PageRank over the import graph -- personalised to the files that matched --
as the tie-breaker (Aider's repo-map idea, Python `ast` only). Test files are
indexed separately: they are evidence (harvested inputs, conventions), not
edit locations.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from raven.crux.issue import IssueCard
from raven.repo.files import list_repo_files


@dataclass
class FunctionInfo:
    path: str            # repo-relative
    qualname: str        # Class.method or function
    name: str
    start: int
    end: int
    signature: str
    doc: str = ""
    names_used: set = field(default_factory=set)
    score: float = 0.0

    @property
    def module(self) -> str:
        return module_name(self.path)

    def skeleton(self) -> str:
        doc = f'  # {self.doc}' if self.doc else ""
        return f"{self.path}:{self.start}  {self.signature}{doc}"


def module_name(path: str) -> str:
    """Import path for a repo-relative .py file (src/ layout aware)."""
    parts = list(Path(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def is_test_file(path: str) -> bool:
    p = Path(path)
    return p.name.startswith("test_") or p.name.endswith("_test.py") or "tests" in p.parts or p.name == "conftest.py"


def _signature(src_lines: list[str], node) -> str:
    """The `def ...:` header, joined onto one line (multi-line signatures
    included, comments dropped)."""
    out = []
    for line in src_lines[node.lineno - 1: node.lineno + 7]:
        code = line.split("#", 1)[0].rstrip()
        out.append(code.strip())
        if code.endswith(":"):
            break
    return " ".join(out)


class RepoMap:
    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root).resolve()
        self.functions: list[FunctionInfo] = []
        self.test_files: list[str] = []
        self.sources: dict[str, str] = {}
        self.imports: dict[str, set] = {}   # file -> modules it imports
        self._index()

    # -- indexing ---------------------------------------------------------

    def _index(self) -> None:
        for rel in list_repo_files(self.repo_root):
            if not rel.endswith(".py"):
                continue
            try:
                src = (self.repo_root / rel).read_text(errors="replace")
                tree = ast.parse(src)
            except (OSError, SyntaxError, ValueError):
                continue
            self.sources[rel] = src
            self.imports[rel] = _imported_modules(tree)
            if is_test_file(rel):
                self.test_files.append(rel)
                continue
            lines = src.splitlines()
            for node, owner in _walk_defs(tree):
                qual = f"{owner}.{node.name}" if owner else node.name
                self.functions.append(FunctionInfo(
                    path=rel, qualname=qual, name=node.name, start=node.lineno,
                    end=getattr(node, "end_lineno", node.lineno), signature=_signature(lines, node),
                    doc=(ast.get_docstring(node) or "").strip().splitlines()[0][:100] if ast.get_docstring(node) else "",
                    names_used={n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                    | {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)},
                ))

    # -- ranking ----------------------------------------------------------

    def rank(self, card: IssueCard) -> list[FunctionInfo]:
        idents = set(card.identifiers)
        lowered = card.text.lower()
        seeds: dict[str, float] = {}
        for fn in self.functions:
            s = 0.0
            for f, _line, frame_fn in card.frames:
                if f.endswith(fn.path) or fn.path.endswith(Path(f).name):
                    s += 6
                    if frame_fn == fn.name:
                        s += 15
            if fn.name in idents:
                s += 10
            owner = fn.qualname.split(".")[0] if "." in fn.qualname else ""
            if owner and owner in idents:
                s += 5
            if any(fn.path.endswith(p) or p.endswith(fn.path) for p in card.paths):
                s += 6
            elif Path(fn.path).stem.lower() in lowered and len(Path(fn.path).stem) > 3:
                s += 2
            s += min(5, len(fn.names_used & idents))
            fn.score = s
            if s > 0:
                seeds[fn.path] = seeds.get(fn.path, 0) + s
        pr = self._pagerank(seeds)
        for fn in self.functions:
            fn.score += 3 * pr.get(fn.path, 0.0)
        return sorted(self.functions, key=lambda f: (-f.score, f.path, f.start))

    def _pagerank(self, seeds: dict[str, float], iters: int = 20, damping: float = 0.85) -> dict[str, float]:
        files = list(self.sources)
        if not files or not seeds:
            return {}
        by_module = {module_name(f): f for f in files}
        edges = {f: [by_module[m] for m in self.imports.get(f, ()) if m in by_module and by_module[m] != f]
                 for f in files}
        total = sum(seeds.values())
        pers = {f: seeds.get(f, 0.0) / total for f in files}
        rank = dict(pers)
        for _ in range(iters):
            nxt = {f: (1 - damping) * pers[f] for f in files}
            for f, outs in edges.items():
                if outs:
                    share = damping * rank[f] / len(outs)
                    for o in outs:
                        nxt[o] += share
            rank = nxt
        top = max(rank.values()) or 1.0
        return {f: v / top for f, v in rank.items()}

    # -- context ----------------------------------------------------------

    def find(self, path: str, symbol: str) -> FunctionInfo | None:
        name = symbol.split(".")[-1].split("(")[0].strip()
        path = path.strip().lstrip("./")
        exact = [f for f in self.functions if (f.path == path or f.path.endswith(path) or not path)
                 and (f.qualname == symbol or f.name == name)]
        if exact:
            return exact[0]
        anywhere = [f for f in self.functions if f.name == name]
        return anywhere[0] if len(anywhere) == 1 else None

    def span(self, fn: FunctionInfo, context: int = 2, numbered: bool = True) -> str:
        """The function's source (line-numbered for reading; plain for
        prompts whose output must quote it exactly), plus module imports."""
        lines = self.sources[fn.path].splitlines()
        lo, hi = max(0, fn.start - 1 - context), min(len(lines), fn.end + context)
        head = [l for l in lines[:60] if l.startswith(("import ", "from "))][:12]
        body = "\n".join((f"{i + 1:>5}  {lines[i]}" if numbered else lines[i]) for i in range(lo, hi))
        imports = ("\n".join(head) + "\n...\n") if head else ""
        return f"# {fn.path} (module {fn.module})\n{imports}{body}"

    def skeletons(self, ranked: list[FunctionInfo], n: int = 18) -> str:
        return "\n".join(f.skeleton() for f in ranked[:n])

    def siblings(self, fn: FunctionInfo) -> list[FunctionInfo]:
        return [f for f in self.functions if f.path == fn.path and f is not fn]


def _walk_defs(tree):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node, ""
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield sub, node.name


def _imported_modules(tree) -> set:
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            mods.update(f"{node.module}.{a.name}" for a in node.names)
    return mods
