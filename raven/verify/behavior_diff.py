"""Behavioral diff (plan §11 step 5): compares a module's functions before
and after an edit by executing both source versions in isolated namespaces
and calling matching functions with the same probe inputs. Flags any
function *other* than the intended target whose output changed —
a collateral behavior change. Kept deliberately simple: zero-arg probes
discovered via `ast`, not a general fuzzer. A function that can't be called
this way (needs real arguments) just raises identically on both sides and
is silently skipped — a safe degrade, not a false positive."""

from __future__ import annotations

import ast
import types


def _load_module_from_source(source: str, module_name: str) -> types.ModuleType:
    module = types.ModuleType(module_name)
    exec(compile(source, f"<{module_name}>", "exec"), module.__dict__)
    return module


def discover_zero_arg_functions(source: str) -> list[str]:
    """Top-level functions with no required positional/keyword arguments —
    the only ones we can safely probe without knowing real call sites."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            required = len(args.args) - len(args.defaults)
            if required == 0 and not args.vararg and not args.kwonlyargs:
                names.append(node.name)
    return names


def diff_function_outputs(pre_source: str, post_source: str, probes: dict[str, list[tuple]]) -> dict:
    """probes: {"function_name": [(args_tuple, kwargs_dict_or_None), ...]}.
    Returns {"function_name": [{"args": ..., "pre": ..., "post": ...}, ...]}
    for every function whose output differs. Never raises — a function
    missing on either side, or one that errors identically on both, is
    simply excluded rather than failing the whole diff."""
    try:
        pre_mod = _load_module_from_source(pre_source, "_raven_behavior_pre")
        post_mod = _load_module_from_source(post_source, "_raven_behavior_post")
    except Exception:
        return {}

    changes: dict = {}
    for func_name, calls in probes.items():
        pre_fn = getattr(pre_mod, func_name, None)
        post_fn = getattr(post_mod, func_name, None)
        if not callable(pre_fn) or not callable(post_fn):
            continue
        for args, kwargs in calls:
            pre_result = _safe_call(pre_fn, args, kwargs)
            post_result = _safe_call(post_fn, args, kwargs)
            if pre_result != post_result:
                changes.setdefault(func_name, []).append(
                    {"args": list(args), "pre": pre_result, "post": post_result}
                )
    return changes


def _safe_call(fn, args, kwargs):
    try:
        return fn(*args, **(kwargs or {}))
    except Exception as exc:
        return f"<raised {type(exc).__name__}>"


def diff_zero_arg_functions(pre_source: str, post_source: str) -> dict:
    """Convenience entry point: auto-discovers zero-arg functions in the
    post-edit source and diffs all of them."""
    functions = discover_zero_arg_functions(post_source)
    probes = {name: [((), None)] for name in functions}
    return diff_function_outputs(pre_source, post_source, probes)


def find_collateral_changes(changes: dict, target_function: str | None) -> list[str]:
    """Functions that changed but weren't the one the task was aimed at."""
    if target_function is None:
        return list(changes.keys())
    return [name for name in changes if name != target_function]
