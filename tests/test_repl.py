"""The CLI intake paths the evaluator can use after `make run` (submission
rules §5): piped stdin, --issue, RAVEN_ISSUE — all must run the full
autonomous pipeline against the right repository."""

import io
import shutil
from pathlib import Path

import pytest

import raven.cli as cli
from raven.config import REPO_ROOT
from raven.intake import expand_issue_refs, is_harness_repo, resolve_repo
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway

TOY_REPO = Path(__file__).parent / "fixtures" / "toy_repo"
FIX = [
    '```action\n{"tool": "edit", "args": {"path": "calc/arithmetic.py", '
    '"search": "return sum(numbers) / (len(numbers) + 1)", "replace": "return sum(numbers) / len(numbers)"}}\n```',
    '```action\n{"tool": "done", "args": {"summary": "fixed average()"}}\n```',
]


class _Stdin(io.StringIO):
    def isatty(self):
        return False


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(TOY_REPO, dest)
    return dest


@pytest.fixture
def fake_model(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.delenv("RAVEN_ISSUE", raising=False)
    monkeypatch.delenv("RAVEN_REPO", raising=False)
    monkeypatch.setattr(cli, "build_gateway", lambda config: LLMGateway(FakeClient(list(FIX) + ["{}"] * 4)))


def test_piped_issue_runs_the_full_autonomous_pipeline(repo, fake_model, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _Stdin("average([2,4,6]) returns 3.0 instead of 4"))
    code = cli.main(["--repo", str(repo)])

    out = capsys.readouterr().out
    assert code == 0
    assert "===== RAVEN RESULT =====" in out and "accepted: True" in out
    assert "return sum(numbers) / len(numbers)" in (repo / "calc" / "arithmetic.py").read_text()


def test_raven_issue_and_raven_repo_env_vars(repo, fake_model, monkeypatch, capsys):
    monkeypatch.setenv("RAVEN_ISSUE", "average() is off by one")
    monkeypatch.setenv("RAVEN_REPO", str(repo))
    monkeypatch.setattr("sys.stdin", _Stdin(""))
    assert cli.main([]) == 0
    assert "accepted: True" in capsys.readouterr().out


def test_scored_run_without_api_key_fails_loudly(repo, monkeypatch, capsys):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin", _Stdin("fix it"))
    assert cli.main(["--repo", str(repo)]) == 2
    assert "AI_API_KEY is not set" in capsys.readouterr().err


def test_refuses_to_edit_raven_itself_and_says_how_to_pick_a_repo(fake_model, monkeypatch, capsys):
    monkeypatch.chdir(REPO_ROOT)  # exactly where the evaluator runs `make run`
    monkeypatch.setattr("sys.stdin", _Stdin("fix the bug"))
    assert cli.main([]) == 2
    err = capsys.readouterr().err
    assert "its own source tree" in err and "RAVEN_REPO" in err and "--repo" in err


def test_repo_resolution_order(tmp_path, monkeypatch):
    monkeypatch.setenv("RAVEN_REPO", str(tmp_path))
    assert resolve_repo(None) == tmp_path.resolve()
    assert resolve_repo(str(REPO_ROOT)) == REPO_ROOT.resolve()  # --repo wins
    assert is_harness_repo(REPO_ROOT) and not is_harness_repo(tmp_path)


def test_github_issue_url_is_expanded_with_the_issue_text():
    calls = []

    def fake_fetch(owner, repo, number):
        calls.append((owner, repo, number))
        return "average() off by one\n\naverage([2,4,6]) returns 3.0"

    text = expand_issue_refs("please fix https://github.com/acme/calc/issues/12", fetch=fake_fetch)
    assert calls == [("acme", "calc", "12")]
    assert "average([2,4,6]) returns 3.0" in text and "acme/calc#12" in text
    # unfetchable: left untouched
    assert expand_issue_refs("https://github.com/a/b/issues/1", fetch=lambda *a: None) == "https://github.com/a/b/issues/1"
