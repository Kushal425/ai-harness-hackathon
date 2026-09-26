"""Execution story (plan §11 step 3): traces a single test's function-call
sequence via sys.settrace, run in a subprocess (so the trace never pollutes
our own process), and compresses it to ~30 lines — function entries plus
the exception line. Stdlib only. Enriches the report; a tracing failure
here must never affect the pass/fail verdict, so every failure mode
degrades to an explanatory string rather than raising."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from raven.repo.interpreter import target_python
from raven.tools.shell import target_code_env

_TRACER_SCRIPT = r'''
import sys, os, json, importlib.util, traceback

target = sys.argv[1]  # "path/to/test_file.py::test_func"
repo_root = sys.argv[2]
path_str, func_name = target.split("::")
# absolute, so frames' co_filename contain repo_root on every Python
# version (3.9 keeps a relative path as given, which filtered out the
# test's own frames)
path_str = os.path.join(repo_root, path_str)
sys.path.insert(0, repo_root)

events = []

def tracer(frame, event, arg):
    if event == "call" and frame.f_code.co_name != "<module>" and repo_root in frame.f_code.co_filename:
        events.append((frame.f_code.co_name, frame.f_lineno))
    return tracer

outcome = "PASSED"
exc_line = None
try:
    spec = importlib.util.spec_from_file_location("_traced_test_module", path_str)
    module = importlib.util.module_from_spec(spec)
    sys.settrace(tracer)
    spec.loader.exec_module(module)
    getattr(module, func_name)()
except Exception as exc:
    outcome = f"{type(exc).__name__}: {exc}"
    tb = traceback.extract_tb(exc.__traceback__)
    exc_line = tb[-1].lineno if tb else None
finally:
    sys.settrace(None)

print(json.dumps({"events": events, "outcome": outcome, "exc_line": exc_line}))
'''


def trace_test(repo_root: Path, test_target: str, timeout: int = 30) -> str:
    """test_target like 'tests/test_arithmetic.py::test_average'. Returns a
    compact ~30-line execution story."""
    if "::" not in test_target:
        return f"(trace skipped: expected 'path::function', got {test_target!r})"

    script_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(_TRACER_SCRIPT)
            script_path = f.name

        proc = subprocess.run(
            [target_python(repo_root), script_path, test_target, str(Path(repo_root).resolve())],
            cwd=str(repo_root), env=target_code_env(repo_root), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "(trace timed out)"
    except OSError as exc:
        return f"(trace failed to launch: {exc})"
    finally:
        if script_path:
            Path(script_path).unlink(missing_ok=True)

    stdout = proc.stdout.strip()
    if not stdout:
        return f"(trace produced no output: {proc.stderr.strip()[:200]})"

    try:
        data = json.loads(stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return f"(trace output unparseable: {stdout[:200]})"

    return _compress(data)


def _compress(data: dict, max_lines: int = 30) -> str:
    events = data.get("events", [])
    lines = [f"outcome: {data.get('outcome')}"]
    calls = [f"-> {func}() at line {lineno}" for func, lineno in events]
    lines += calls[: max_lines - 2]
    if len(calls) > max_lines - 2:
        lines.append(f"... ({len(calls) - (max_lines - 2)} more calls omitted)")
    if data.get("exc_line"):
        lines.append(f"exception raised at line {data['exc_line']}")
    return "\n".join(lines)
