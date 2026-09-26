"""Disagreement: find where candidate fixes actually differ, and the one
input that best separates them.

Inputs come from (cheapest first, all deterministic):
  1. the probe's own cases (the issue's example),
  2. type-aware variants of those calls' literal arguments -- empty, zero,
     negative, off-by-one, truncated, duplicated ("issue twins"),
  3. calls to the same functions harvested from the repo's tests, run with
     that test file's imports (the repo's own conventions, "house rules").
Every alive candidate (and the original code) is run on all of them in one
subprocess each; candidates with identical outcome vectors form a cluster.
Only inputs whose outcomes differ BETWEEN clusters need an oracle, and of
those we ask about the one that splits the clusters most evenly (maximum
partition entropy = maximum information per question).
"""

from __future__ import annotations

import ast
import builtins
import copy
import math
from dataclasses import dataclass

from raven.crux.probe import Case, Probe
from raven.crux.repomap import RepoMap

ORIGIN_PRIORITY = {"probe": 0, "harvested": 1, "variant": 2, "model": 3}
_BUILTINS = set(dir(builtins))


# -- input generation -------------------------------------------------------

def _literal_variants(node: ast.AST) -> list[ast.AST]:
    out: list[ast.AST] = []
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool):
            out.append(ast.Constant(not v))
        elif isinstance(v, int):
            out += [ast.Constant(x) for x in dict.fromkeys([0, 1, -1, v - 1, v + 1]) if x != v]
        elif isinstance(v, float):
            out += [ast.Constant(x) for x in dict.fromkeys([0.0, -v, v / 2]) if x != v]
        elif isinstance(v, str):
            cands = ["", " ", v[:1], v + v, v.upper(), " " + v + " "]
            out += [ast.Constant(x) for x in dict.fromkeys(cands) if x != v]
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        out += [ast.Constant(0), ast.Constant(abs(node.operand.value))]
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        elts = node.elts
        kind = type(node)
        make = (lambda e: kind(elts=e, ctx=ast.Load())) if kind is not ast.Set else (lambda e: ast.Set(elts=e))
        if elts:
            out.append(ast.List(elts=[], ctx=ast.Load()) if kind is ast.Set else make([]))
            out.append(make(elts[:1]))
            if len(elts) > 1:
                out.append(make(list(reversed(elts))))
            out.append(make(elts + elts[:1]))
        else:
            out.append(make([ast.Constant(0)]))
    elif isinstance(node, ast.Dict) and node.keys:
        out.append(ast.Dict(keys=[], values=[]))
    return out


def variant_cases(probe: Probe, cap: int = 24, per_case: int = 10) -> list[Case]:
    seen = {c.expr for c in probe.cases}
    out: list[Case] = []
    for case in probe.cases:
        try:
            tree = ast.parse(case.expr, mode="eval")
        except SyntaxError:
            continue
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        made = 0
        for call in calls[:2]:
            slots = [(call.args, i) for i in range(len(call.args))] + \
                    [(call.keywords, i) for i in range(len(call.keywords))]
            for container, i in slots:
                target = container[i].value if container is call.keywords else container[i]
                for replacement in _literal_variants(target):
                    if made >= per_case or len(out) >= cap:
                        break
                    new_tree = copy.deepcopy(tree)
                    new_call = [n for n in ast.walk(new_tree) if isinstance(n, ast.Call)][calls.index(call)]
                    new_container = new_call.keywords if container is call.keywords else new_call.args
                    if new_container is new_call.keywords:
                        new_container[i].value = replacement
                    else:
                        new_container[i] = replacement
                    expr = ast.unparse(ast.fix_missing_locations(new_tree))
                    if expr not in seen:
                        seen.add(expr)
                        out.append(Case(expr=expr, origin="variant"))
                        made += 1
    return out


def harvested_cases(rmap: RepoMap, names: set[str], cap: int = 12) -> list[Case]:
    """Calls to `names` found in the repo's tests, runnable in isolation:
    every free name must be a builtin, a target name, or something the test
    module imports at top level."""
    out: list[Case] = []
    seen: set[str] = set()
    for test in rmap.test_files:
        src = rmap.sources.get(test, "")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        import_nodes = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        imported = set()
        for n in import_nodes:
            for a in n.names:
                imported.add((a.asname or a.name).split(".")[0])
        setup = "\n".join(ast.get_source_segment(src, n) or "" for n in import_nodes)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            if fname not in names:
                continue
            expr = ast.get_source_segment(src, node)
            if not expr or expr in seen or len(expr) > 200:
                continue
            free = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            if not free <= (_BUILTINS | imported | names):
                continue
            seen.add(expr)
            out.append(Case(expr=expr, setup=setup, origin="harvested"))
            if len(out) >= cap:
                return out
    return out


# -- clustering and the crux ------------------------------------------------

@dataclass
class Cluster:
    members: list[str]          # candidate ids
    fingerprint: tuple

    @property
    def label(self) -> str:
        return ", ".join(self.members)


def cluster(fingerprints: dict[str, tuple]) -> list[Cluster]:
    groups: dict[tuple, list[str]] = {}
    for cid, fp in fingerprints.items():
        groups.setdefault(fp, []).append(cid)
    return sorted((Cluster(m, fp) for fp, m in groups.items()), key=lambda c: (-len(c.members), c.members))


def disagreement_indices(clusters: list[Cluster]) -> list[int]:
    if len(clusters) < 2:
        return []
    n = len(clusters[0].fingerprint)
    return [i for i in range(n) if len({c.fingerprint[i] for c in clusters}) > 1]


def partition_entropy(clusters: list[Cluster], i: int) -> float:
    weights: dict[str, int] = {}
    for c in clusters:
        weights[c.fingerprint[i]] = weights.get(c.fingerprint[i], 0) + len(c.members)
    total = sum(weights.values())
    return -sum(w / total * math.log2(w / total) for w in weights.values())


def best_crux(clusters: list[Cluster], cases: list[Case]) -> int | None:
    idx = disagreement_indices(clusters)
    if not idx:
        return None
    return max(idx, key=lambda i: (round(partition_entropy(clusters, i), 6),
                                   -ORIGIN_PRIORITY.get(cases[i].origin, 9), -len(cases[i].expr)))
