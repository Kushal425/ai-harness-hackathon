import shutil
from pathlib import Path

from raven.repo.digest import build_digest

TOY_REPO = Path(__file__).parent / "fixtures" / "toy_repo"


def test_build_digest_finds_test_command_and_symbols(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(TOY_REPO, dest)
    digest = build_digest(dest, use_cache=False)
    assert digest.test_command == "pytest"
    assert digest.language == "python"
    assert any("average" in s for s in digest.symbols)
    assert "calc/arithmetic.py" in digest.file_tree


def test_digest_summary_is_bounded(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(TOY_REPO, dest)
    digest = build_digest(dest, use_cache=False)
    summary = digest.summary()
    assert "test command: pytest" in summary
