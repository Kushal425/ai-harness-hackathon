"""Reviewer sub-agent (plan §7.3): an independent critique of the final
diff against the task, in a fresh context that never saw the executor's
own reasoning — avoiding self-grading bias. Runs once per task and skips
trivial diffs (plan §7.3's cost-control note)."""

from __future__ import annotations

import subprocess

from raven.agents.base import run_subagent
from raven.prompts import get_prompt
from raven.tools.git_tool import is_repo_toplevel

# Text lives in prompts/base.yaml (plan §12.3).
REVIEWER_SYSTEM_PROMPT = get_prompt("reviewer_system")

TRIVIAL_DIFF_LINE_THRESHOLD = 5


def _diff_line_count(diff_text: str) -> int:
    return sum(
        1 for line in diff_text.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )


def review_diff(gateway, repo_root, evidence: dict | None) -> str:
    if not is_repo_toplevel(repo_root):
        return "no diff to review"
    proc = subprocess.run(["git", "-C", str(repo_root), "diff"], capture_output=True, text=True)
    diff_text = proc.stdout.strip() if proc.returncode == 0 else ""
    if not diff_text:
        return "no diff to review"

    if _diff_line_count(diff_text) <= TRIVIAL_DIFF_LINE_THRESHOLD:
        return f"diff is trivial (<= {TRIVIAL_DIFF_LINE_THRESHOLD} changed lines) — skipping Reviewer to save tokens"

    task_text = f"Diff:\n{diff_text}\n\nEvidence gathered:\n{evidence}"
    data = run_subagent(gateway, REVIEWER_SYSTEM_PROMPT, task_text, "review")
    if not data:
        return "Reviewer: could not produce a structured review"

    verdict = data.get("verdict", "unknown")
    issues = data.get("issues", [])
    if not issues:
        return f"verdict: {verdict} (no issues found)"
    lines = [f"verdict: {verdict}"]
    lines += [f"- [{issue.get('severity', '?')}] {issue.get('description', '')}" for issue in issues]
    return "\n".join(lines)
