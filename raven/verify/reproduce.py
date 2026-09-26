"""Reproduce (plan §11 step 2).

Two pieces:
- Pre-fix / post-fix suite capture: thin wrappers around
  baseline.run_test_summary — skipped for task types where tests aren't the
  evidence (static_only/answer_only), and entirely if the repo has no
  detected test command.
- The reproduction test: the executor is asked to write
  .raven/repro/test_repro.py (failing on the original code) before fixing.
  `verify_repro` then checks it independently of the model: it swaps the
  original files back in, runs the test (must FAIL), restores the fix, and
  runs it again (must PASS). This is what makes "fixed" provable when the
  repo has no failing test for the issue — e.g. when the graders' tests
  are hidden."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from raven.core.understand import Understanding
from raven.recovery.checkpoints import CheckpointManager
from raven.repo.digest import RepoDigest
from raven.tools.registry import RunContext, ToolRegistry
from raven.verify.baseline import TestSummary, run_test_summary

NO_TEST_EVIDENCE_STRATEGIES = {"static_only", "answer_only"}


def _should_run_tests(digest: RepoDigest, understanding: Understanding | None) -> bool:
    if digest.test_command is None:
        return False
    if understanding is not None and understanding.verification_strategy in NO_TEST_EVIDENCE_STRATEGIES:
        return False
    return True


def capture_pre_fix(
    registry: ToolRegistry, ctx: RunContext, digest: RepoDigest, understanding: Understanding | None = None
) -> TestSummary | None:
    if not _should_run_tests(digest, understanding):
        return None
    return run_test_summary(registry, ctx)


def capture_post_fix(
    registry: ToolRegistry, ctx: RunContext, digest: RepoDigest, understanding: Understanding | None = None
) -> TestSummary | None:
    if not _should_run_tests(digest, understanding):
        return None
    return run_test_summary(registry, ctx)


REPRO_PATH = ".raven/repro/test_repro.py"
# task types whose outcome is a code change a test can pin down
REPRO_TASK_TYPES = {"bug_fix", "feature", "performance", "config"}


_NO_REPRO_GOAL_RE = re.compile(
    r"\b(refactor\w*|renam\w*|docstrings?|document\w*|comments?|(?:add|write)\w* (?:[\w-]+ ){0,3}tests?|test coverage|format\w*|lint\w*)\b",
    re.IGNORECASE,
)


def repro_applicable(
    digest: RepoDigest, understanding: Understanding | None, expects_changes: bool, goal: str = "",
) -> bool:
    if digest.test_command != "pytest" or not expects_changes:
        return False
    if understanding is not None:
        return understanding.task_type in REPRO_TASK_TYPES
    # single_loop has no task type: skip the goals that plainly aren't about
    # behaviour a failing test could pin down
    return not _NO_REPRO_GOAL_RE.search(goal)


def clear_repro(repo_root: Path) -> None:
    """A stale reproduction from an earlier run must never be mistaken for
    this run's."""
    (Path(repo_root) / REPRO_PATH).unlink(missing_ok=True)


def _run_repro(registry: ToolRegistry, ctx: RunContext) -> tuple[bool, str]:
    result = registry.dispatch("tests", {"target": REPRO_PATH}, ctx)
    return result.ok, result.output


def verify_repro(registry: ToolRegistry, ctx: RunContext, checkpoints: CheckpointManager) -> dict | None:
    """Fail-before / pass-after check of the model's reproduction test, or
    None if the model didn't write one. The working tree is always left
    exactly as the executor left it (try/finally)."""
    repo_root = Path(ctx.repo_root)
    repro_file = repo_root / REPRO_PATH
    if not repro_file.is_file():
        return None

    passes_after, after_output = _run_repro(registry, ctx)

    patch = checkpoints.touched_paths
    post_state = {}
    for path in patch:
        target = repo_root / path
        post_state[path] = target.read_text(errors="replace") if target.exists() else None
    try:
        for path in patch:
            original = checkpoints.original_content(path)
            target = repo_root / path
            if original is None:
                target.unlink(missing_ok=True)  # file the patch created
            else:
                target.write_text(original)
        passes_before, before_output = _run_repro(registry, ctx)
    finally:
        for path, content in post_state.items():
            target = repo_root / path
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)

    return {
        "test": REPRO_PATH,
        "failed_before": not passes_before,
        "passes_after": passes_after,
        "verified": (not passes_before) and passes_after,
        "before_tail": "\n".join(before_output.splitlines()[-6:]),
        "after_tail": "\n".join(after_output.splitlines()[-6:]),
    }


def archive_repro(repo_root: Path, run_dir: Path, repro: dict | None) -> None:
    """Moves the reproduction test into the run's report folder, so the
    next run starts clean and the evidence stays auditable."""
    repro_file = Path(repo_root) / REPRO_PATH
    if not repro_file.is_file():
        return
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(repro_file), str(run_dir / "test_repro.py"))
        if repro is not None:
            archived = run_dir / "test_repro.py"
            try:
                repro["test"] = str(archived.relative_to(Path(repo_root)))
            except ValueError:
                repro["test"] = str(archived)
    except OSError:
        pass
