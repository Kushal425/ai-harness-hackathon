"""Pre-fix / post-fix test capture (plan §11 step 2: reproduce). Thin
wrappers around baseline.run_test_summary — skipped for task types where
tests aren't the evidence (static_only/answer_only), and skipped entirely
if the repo has no detected test command."""

from __future__ import annotations

from raven.core.understand import Understanding
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
