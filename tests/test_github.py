"""GitHub integration: auth, discovery, issues, isolated workspaces, and the
issue -> Crux flow. GitHub's API is mocked (httpx.MockTransport); cloning
and worktrees are real, against a local bare repository served as the
"remote" over file://, so nothing touches the network or Raven's own
workspaces/ directory."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import httpx
import pytest

from raven import github as gh
from raven import workspace

TOY = Path(__file__).parent / "fixtures" / "toy_repo"
DEMO = Path(__file__).parent / "fixtures" / "crux_demo"


# -- helpers ------------------------------------------------------------------

def _run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """A 'GitHub' with one repository, acme/listkit (the crux demo), as a
    bare git repo reachable at file://<tmp>/remotes/acme/listkit.git; and
    a private workspaces dir."""
    src = tmp_path / "src"
    shutil.copytree(DEMO, src, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    _run("git", "init", "-q", "-b", "main", cwd=src)
    _run("git", "add", "-A", cwd=src)
    _run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=src)
    _run("git", "checkout", "-q", "-b", "dev", cwd=src)
    _run("git", "checkout", "-q", "main", cwd=src)
    bare = tmp_path / "remotes" / "acme" / "listkit.git"
    bare.parent.mkdir(parents=True)
    _run("git", "clone", "-q", "--bare", str(src), str(bare))
    monkeypatch.setattr(gh, "WEB", f"file://{tmp_path / 'remotes'}")
    monkeypatch.setattr(workspace, "WORKSPACES_DIR", tmp_path / "workspaces")
    return tmp_path


def _api(routes: dict, seen: list | None = None):
    """MockTransport: {"/path": json | callable(request)} ; unknown -> 404."""
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"message": "Not Found"})
        body = body(request) if callable(body) else body
        return body if isinstance(body, httpx.Response) else httpx.Response(200, json=body)
    return httpx.MockTransport(handler)


ISSUE_7 = {
    "number": 7, "title": "chunk() drops the last partial chunk", "state": "open", "comments": 2,
    "html_url": "https://github.com/acme/listkit/issues/7",
    "body": "chunk([1, 2, 3, 4, 5], 2) returns [[1, 2], [3, 4]] -- the last partial chunk [5] is dropped.",
    "labels": [{"name": "bug"}, {"name": "good first issue"}],
}
COMMENTS_7 = [
    {"user": {"login": "maintainer", "type": "User"}, "body": "Confirmed. Empty input should still give []."},
    {"user": {"login": "ci-bot", "type": "Bot"}, "body": "Build #123 passed"},
]
TIMELINE_7 = [{"event": "cross-referenced", "source": {"issue": {
    "number": 9, "title": "WIP: fix chunk", "pull_request": {"merged_at": None}}}}]


def _client(extra: dict | None = None, seen=None, token="tok-123"):
    routes = {
        "/user": {"login": "sachin"},
        "/repos/acme/listkit": {"full_name": "acme/listkit", "owner": {"login": "acme"}, "name": "listkit",
                                "private": True, "default_branch": "main"},
        "/repos/acme/listkit/issues/7": ISSUE_7,
        "/repos/acme/listkit/issues/7/comments": COMMENTS_7,
        "/repos/acme/listkit/issues/7/timeline": TIMELINE_7,
    }
    routes.update(extra or {})
    return gh.GitHubClient(token=token, transport=_api(routes, seen), allow_anonymous=True)


# -- auth ---------------------------------------------------------------------

def test_token_is_saved_outside_the_repo_with_owner_only_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(gh.shutil, "which", lambda name: None)  # ignore any real gh CLI login
    path = gh.save_token("secret-token", "sachin")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert gh.resolve_token() == ("secret-token", "saved login")
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")          # env wins
    assert gh.resolve_token() == ("env-token", "$GITHUB_TOKEN")
    assert gh.forget_token() and not path.exists()


def test_device_flow_polls_through_pending_and_slow_down():
    replies = iter([{"error": "authorization_pending"}, {"error": "slow_down", "interval": 7},
                    {"access_token": "gho_abc", "token_type": "bearer"}])
    shown, slept = [], []

    def handler(request):
        if request.url.path == "/login/device/code":
            assert b"client_id=Iv1.test" in request.content and b"scope=repo" in request.content
            return httpx.Response(200, json={"device_code": "d", "user_code": "ABCD-1234", "interval": 5,
                                             "verification_uri": "https://github.com/login/device", "expires_in": 900})
        return httpx.Response(200, json=next(replies))

    token = gh.device_login("Iv1.test", on_code=shown.append, transport=httpx.MockTransport(handler),
                            sleep=slept.append)
    assert token == "gho_abc"
    assert "ABCD-1234" in shown[0] and slept == [5, 5, 7]


def test_device_flow_needs_a_client_id_and_reports_denial():
    with pytest.raises(gh.GitHubAuthError, match="RAVEN_GITHUB_CLIENT_ID"):
        gh.device_login("")

    def handler(request):
        if request.url.path == "/login/device/code":
            return httpx.Response(200, json={"device_code": "d", "user_code": "X", "interval": 1,
                                             "verification_uri": "u", "expires_in": 60})
        return httpx.Response(200, json={"error": "access_denied"})

    with pytest.raises(gh.GitHubAuthError, match="cancelled"):
        gh.device_login("Iv1.test", on_code=lambda m: None, transport=httpx.MockTransport(handler),
                        sleep=lambda s: None)


def test_expired_token_is_an_auth_error():
    client = gh.GitHubClient(token="dead", transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"message": "Bad credentials"})))
    with pytest.raises(gh.GitHubAuthError, match="expired or revoked"):
        client.viewer()


# -- validation -----------------------------------------------------------------

def test_untrusted_names_urls_and_branches_are_validated():
    assert gh.parse_repo("acme/listkit") == ("acme", "listkit")
    assert gh.parse_repo("https://github.com/acme/listkit.git") == ("acme", "listkit")
    for bad in ("acme", "../etc/passwd", "acme/..", "acme/-rf", "a/b/c", "acme/listkit; rm -rf ~"):
        with pytest.raises(ValueError):
            gh.parse_repo(bad)
    assert gh.parse_issue_ref("see https://github.com/acme/listkit/issues/7 please") == ("acme", "listkit", 7)
    assert gh.parse_issue_ref("https://github.com/../x/issues/1") is None
    assert workspace.valid_branch("feature/x") and not workspace.valid_branch("--upload-pack=touch /tmp/p")
    assert not workspace.valid_branch("a..b")


# -- discovery ----------------------------------------------------------------

def test_repository_listing_is_paginated_and_carries_metadata():
    def page(request):
        n = int(request.url.params["page"])
        count = 100 if n == 1 else 3
        return [{"full_name": f"acme/r{n}-{i}", "owner": {"login": "acme"}, "name": f"r{n}-{i}",
                 "private": i % 2 == 0, "default_branch": "main", "language": "Python",
                 "open_issues_count": i, "pushed_at": "2026-09-01T00:00:00Z",
                 "permissions": {"admin": False, "push": i == 0, "pull": True}} for i in range(count)]

    seen = []
    repos = _client({"/user/repos": page}, seen).repos()
    assert len(repos) == 103
    assert seen[0].url.params["affiliation"] == "owner,collaborator,organization_member"
    assert repos[0].private and repos[0].permission == "write" and repos[1].permission == "read"
    assert "acme/r1-0  (private, default main, Python, 0 open issues, updated 2026-09-01, write)" == repos[0].line()


def test_issue_fetch_keeps_discussion_and_links_but_not_bots_or_prs():
    listing = [{"number": 7, "title": "chunk bug", "labels": [{"name": "bug"}]},
               {"number": 9, "title": "WIP PR", "pull_request": {}}]
    client = _client({"/repos/acme/listkit/issues": listing})
    assert [i.number for i in client.issues("acme", "listkit")] == [7]
    issue = client.issue("acme", "listkit", 7)
    assert issue.labels == ["bug", "good first issue"]
    assert issue.comments == [("maintainer", "Confirmed. Empty input should still give [].")]  # bot dropped
    assert issue.linked == ["PR #9: WIP: fix chunk"]
    task = issue.to_task()
    assert task.startswith("The task is described by the GitHub issue below") and "<<<ISSUE" in task
    assert "Empty input should still give []" in task and "tok-123" not in task


# -- isolated workspaces ------------------------------------------------------

def test_clone_and_worktrees_are_isolated(remote):
    base = workspace.ensure_clone("acme", "listkit", web=gh.WEB)
    wt1 = workspace.new_worktree(base, "main", "issue-7")
    wt2 = workspace.new_worktree(base, "dev", "issue-8")
    (wt1 / "listkit" / "chunks.py").write_text("# edited\n")
    assert (base / "listkit" / "chunks.py").read_text() != "# edited\n"          # base untouched
    assert (wt2 / "listkit" / "chunks.py").read_text() != "# edited\n"           # other task untouched
    assert "+# edited" in workspace.worktree_diff(wt1)
    assert workspace.remote_branches(base) == ["dev", "main"]
    with pytest.raises(workspace.WorkspaceError, match="not found"):
        workspace.new_worktree(base, "no-such-branch", "x")
    assert workspace.ensure_clone("acme", "listkit", web=gh.WEB) == base         # reuse = fetch, not re-clone


def test_token_reaches_git_only_through_the_environment():
    env = workspace.git_env("s3cret", "https://github.com")
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert "s3cret" not in env["GIT_CONFIG_VALUE_0"]  # base64 in an auth header, never in a URL/argv
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_CONFIG_KEY_0" not in workspace.git_env(None)


# -- the full flow: issue -> isolated worktree -> Crux -> patch ----------------

def _crux_script():
    from tests.test_crux import CAND_A, CAND_B, CAND_C, LOCALIZE, PROBE, VERDICT
    return [LOCALIZE, PROBE, CAND_A, CAND_B, CAND_C, VERDICT]


def test_gh_issue_command_runs_the_agent_in_an_isolated_worktree(remote, monkeypatch):
    from raven.config import load_config
    from raven.llm.fake import FakeClient
    from raven.llm.gateway import LLMGateway
    from raven.session.manager import SessionManager

    config = load_config()
    config.raw["executor"]["parallel_calls"] = False
    client = FakeClient(_crux_script())
    session = SessionManager(config, LLMGateway(client), Path(remote), on_event=lambda e, d: None)
    session._gh = _client()
    assert "working on acme/listkit @ main" in session.handle_input("/gh use acme/listkit")
    reply = session.handle_input("/gh issue 7")

    assert "RESOLVED" in reply and "patch:" in reply and "nothing was pushed" in reply
    worktree = session.state.repo_root
    assert worktree.parent.name == "tasks" and worktree.name.startswith("issue-7-")
    patch = Path(reply.split("patch: ")[1].split(" (")[0]).read_text()
    assert "range(0, len(items), size)" in patch
    base = worktree.parent.parent / "base"
    assert subprocess.run(["git", "-C", str(base), "status", "--porcelain"],
                          capture_output=True, text=True).stdout == ""                # base clone clean
    prompt = "\n".join(m.content for call in client.calls for m in call)
    assert "Empty input should still give []" in prompt       # the maintainer's comment reached the model
    assert "tok-123" not in prompt                            # the token never did


def test_cli_github_issue_url_needs_no_local_repo(remote, monkeypatch, capsys):
    import raven.cli as cli
    from raven.llm.fake import FakeClient
    from raven.llm.gateway import LLMGateway

    monkeypatch.setenv("AI_API_KEY", "k")
    for var in ("RAVEN_REPO", "RAVEN_ISSUE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(cli.REPO_ROOT if hasattr(cli, "REPO_ROOT") else Path(__file__).parent.parent)
    mocked = _client()  # built before patching: _client() itself constructs a GitHubClient
    monkeypatch.setattr(gh, "GitHubClient", lambda *a, **k: mocked)
    monkeypatch.setattr(cli, "build_gateway", lambda config: LLMGateway(FakeClient(_crux_script())))
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False, "read": lambda self: ""})())
    real_kwargs = cli.load_config().run_kwargs

    def sequential_kwargs(self=None):  # scripted replies must be consumed in order
        kw = real_kwargs()
        kw["settings"].parallel_calls = False
        return kw
    monkeypatch.setattr("raven.config.RavenConfig.run_kwargs", lambda self: sequential_kwargs())

    code = cli.main(["--issue", "https://github.com/acme/listkit/issues/7"])
    captured = capsys.readouterr()
    out = captured.out
    assert code == 0, out + captured.err
    assert "fetching acme/listkit#7" in out and "worktree:" in out and "patch:" in out


def test_tui_completion_for_commands_repos_and_issues():
    from raven.ui.tui import _slash_completions

    data = {"commands": ["/gh", "/help", "/repo"], "repos": ["acme/listkit", "acme/other", "me/listkit-fork"],
            "issues": ["7", "71", "9"]}
    assert _slash_completions("/g", data) == ["/gh"]
    assert _slash_completions("/gh i", data) == ["issues", "issue"]
    assert _slash_completions("/gh use list", data) == ["acme/listkit", "me/listkit-fork"]
    assert _slash_completions("/gh issue 7", data) == ["7", "71"]
    assert _slash_completions("hello", data) == []


def test_gh_commands_without_a_login_explain_how_to_connect():
    from raven.config import load_config
    from raven.llm.fake import FakeClient
    from raven.llm.gateway import LLMGateway
    from raven.session.manager import SessionManager

    session = SessionManager(load_config(), LLMGateway(FakeClient([])), Path("."))
    assert "not connected" in session.handle_input("/gh status")
    assert "sign-in needed" in session.handle_input("/gh repos")
    assert "RAVEN_GITHUB_CLIENT_ID" in session.handle_input("/gh login")
    assert "no GitHub repository selected" in session.handle_input("/gh issues")
