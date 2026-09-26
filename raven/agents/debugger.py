"""Debugger sub-agent (plan §10.3): invoked when the executor is stuck on
the same test failure across several edit attempts. A capped 2-call
scientific loop — form competing hypotheses, run the cheapest discriminating
probe, decide which hypothesis survives — then hand the surviving hypothesis
back to the executor as a context nudge. Plain function, not a class
hierarchy; failures anywhere in this degrade to a generic nudge rather than
raising, since a broken Debugger must never crash the run it's trying to
rescue."""

from __future__ import annotations

from raven.core.json_utils import parse_json_block
from raven.llm.protocol import Message
from raven.prompts import get_prompt

# Text lives in prompts/base.yaml (plan §12.3).
HYPOTHESES_PROMPT = get_prompt("debugger_hypotheses")
VERDICT_PROMPT = get_prompt("debugger_verdict")


def run_debugger(gateway, registry, ctx, digest_summary: str, failure_signature: str) -> str:
    task_text = (
        f"Failure signature (repeated):\n{failure_signature}\n\n"
        f"Repository digest:\n{digest_summary}"
    )

    try:
        round1 = gateway.complete(
            [Message(role="system", content=HYPOTHESES_PROMPT), Message(role="user", content=task_text)]
        )
        data = parse_json_block(round1.text, "debug")
        hypotheses = data.get("hypotheses") or []
    except Exception:
        hypotheses = []

    if not hypotheses:
        return f"Debugger: no clear hypothesis formed for: {failure_signature}. Try a different approach."

    first = hypotheses[0]
    probe = first.get("probe") or {}
    tool = probe.get("tool")
    args = probe.get("args") or {}

    probe_result_text = "(no probe specified)"
    if tool:
        try:
            probe_result = registry.dispatch(tool, args, ctx)
            probe_result_text = ("OK: " if probe_result.ok else "ERROR: ") + probe_result.output[:400]
        except Exception as exc:
            probe_result_text = f"probe failed to run: {exc}"

    hypotheses_text = "\n".join(f"- {h.get('hypothesis', '')}" for h in hypotheses[:3])
    verdict_context = (
        f"{task_text}\n\nHypotheses:\n{hypotheses_text}\n\n"
        f"Probe run: {tool}({args})\nProbe result:\n{probe_result_text}"
    )

    try:
        round2 = gateway.complete(
            [Message(role="system", content=VERDICT_PROMPT), Message(role="user", content=verdict_context)]
        )
        verdict_text = round2.text.strip()
    except Exception:
        verdict_text = first.get("hypothesis", "")

    return f"Debugger: {verdict_text}\n(probe {tool}({args}) -> {probe_result_text[:200]})"
