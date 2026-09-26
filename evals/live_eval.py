"""Live benchmark against a REAL model endpoint (no scripted replies).

  .venv/bin/python evals/live_eval.py --base-url http://localhost:11434/v1 --model qwen2.5-coder:7b
      [--strategies crux,single_loop] [--tasks id1,id2] [--budget-s 600]

Every task runs on a fresh copy of tests/fixtures/bugbench and is graded
ONLY by its hidden test (evals/live_tasks.yaml), written into the repo after
the run. Also recorded: Raven's own verdict, so a run Raven calls RESOLVED
that fails the hidden test (a plausible-but-wrong patch) is visible.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from raven.config import RunSettings, load_config  # noqa: E402
from raven.core.orchestrator import run_orchestrator  # noqa: E402
from raven.crux.regression import run_tests  # noqa: E402
from raven.llm.gateway import LLMGateway  # noqa: E402
from raven.llm.providers import OpenAICompatibleClient  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "bugbench"
TASKS = REPO_ROOT / "evals" / "live_tasks.yaml"
RESULTS = REPO_ROOT / "evals" / "results"


def grade(repo: Path, hidden: str) -> bool:
    (repo / "test_hidden_grader.py").write_text(hidden)
    try:
        ran, failed, _ = run_tests(repo, ["test_hidden_grader.py"], timeout=120)
        return ran and not failed
    finally:
        (repo / "test_hidden_grader.py").unlink(missing_ok=True)


def run_one(task: dict, strategy: str, args, log) -> dict:
    repo = Path(tempfile.mkdtemp(prefix=f"raven-live-{task['id']}-")) / "bugbench"
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    client = OpenAICompatibleClient(base_url=args.base_url, api_key=args.api_key, model=args.model,
                                    temperature=0, seed=7, max_output_tokens=args.max_output_tokens,
                                    timeout_s=args.request_timeout, tool_protocol=args.tool_protocol)
    gateway = LLMGateway(client, max_retries=2)
    settings = RunSettings(candidates=args.candidates, parallel_calls=args.parallel, trace=False, lessons=False,
                           budget_seconds=args.budget_s, budget_tokens=args.budget_tokens)
    events = []
    start = time.time()
    try:
        result = run_orchestrator(gateway, repo, task["issue"], strategy=strategy, settings=settings,
                                  max_iterations=args.max_iterations,
                                  on_event=lambda e, d: events.append([e, {k: str(v)[:300] for k, v in d.items()}]))
        accepted, reason, report = result.accepted, result.reason, str(result.report_path)
    except Exception as exc:  # the benchmark must survive anything
        accepted, reason, report = False, f"CRASH: {type(exc).__name__}: {exc}", ""
    seconds = round(time.time() - start, 1)
    passed = grade(repo, task["hidden_test"])
    row = {"id": task["id"], "strategy": strategy, "hidden_pass": passed, "raven_accepted": accepted,
           "false_resolved": accepted and not passed, "calls": gateway.stats.calls,
           "tokens": gateway.stats.total_tokens, "seconds": seconds, "reason": reason[:300], "report": report}
    log.write(json.dumps({**row, "events": events}) + "\n")
    log.flush()
    gateway.close()
    return row


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=os.environ.get("RAVEN_BASE_URL", "http://localhost:11434/v1"))
    p.add_argument("--model", default=os.environ.get("RAVEN_MODEL", "qwen2.5-coder:7b"))
    p.add_argument("--api-key", default=os.environ.get("AI_API_KEY") or "local")
    p.add_argument("--strategies", default="crux,single_loop")
    p.add_argument("--tasks", default="")
    p.add_argument("--candidates", type=int, default=3)
    p.add_argument("--parallel", action="store_true", help="parallel candidate calls (off for local models)")
    p.add_argument("--budget-s", type=float, default=600)
    p.add_argument("--budget-tokens", type=int, default=load_config().run_kwargs()["settings"].budget_tokens)
    p.add_argument("--max-iterations", type=int, default=20)
    p.add_argument("--max-output-tokens", type=int, default=1500)
    p.add_argument("--request-timeout", type=int, default=300)
    p.add_argument("--tool-protocol", default="auto")
    args = p.parse_args()

    tasks = yaml.safe_load(TASKS.read_text())
    if args.tasks:
        wanted = set(args.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]
    RESULTS.mkdir(parents=True, exist_ok=True)
    slug = args.model.replace("/", "_").replace(":", "_")
    log_path = RESULTS / f"live_{slug}.jsonl"
    rows = []
    with log_path.open("a") as log:
        for task in tasks:
            for strategy in args.strategies.split(","):
                row = run_one(task, strategy, args, log)
                rows.append(row)
                print(f"{row['id']:<30} {strategy:<12} hidden={'PASS' if row['hidden_pass'] else 'fail'}  "
                      f"raven={'RESOLVED' if row['raven_accepted'] else 'unresolved'}"
                      f"{'  <-- FALSE RESOLVED' if row['false_resolved'] else ''}  "
                      f"calls={row['calls']} tokens={row['tokens']} {row['seconds']}s  {row['reason'][:90]}", flush=True)
    print()
    for strategy in args.strategies.split(","):
        rs = [r for r in rows if r["strategy"] == strategy]
        if rs:
            print(f"{strategy:<12} hidden-pass {sum(r['hidden_pass'] for r in rs)}/{len(rs)}  "
                  f"false-resolved {sum(r['false_resolved'] for r in rs)}  "
                  f"avg calls {sum(r['calls'] for r in rs) / len(rs):.1f}  "
                  f"avg tokens {sum(r['tokens'] for r in rs) / len(rs):.0f}  "
                  f"avg {sum(r['seconds'] for r in rs) / len(rs):.0f}s")
    print(f"\ndetails: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
