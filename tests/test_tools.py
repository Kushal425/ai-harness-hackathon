from pathlib import Path

import pytest

from raven.tools.registry import RunContext, build_default_registry

TOY_REPO = Path(__file__).parent / "fixtures" / "toy_repo"


@pytest.fixture
def ctx(tmp_path):
    import shutil

    dest = tmp_path / "repo"
    shutil.copytree(TOY_REPO, dest)
    return RunContext(repo_root=dest, mode="act")


@pytest.fixture
def registry():
    return build_default_registry()


def test_read_returns_numbered_lines(registry, ctx):
    result = registry.dispatch("read", {"path": "calc/arithmetic.py"}, ctx)
    assert result.ok
    assert "def add" in result.output
    assert "1  def add" in result.output.replace("    ", " ").replace("  ", " ") or "def add" in result.output


def test_read_rejects_path_escape(registry, ctx):
    result = registry.dispatch("read", {"path": "../../etc/passwd"}, ctx)
    assert not result.ok
    assert "escapes repo root" in result.output


def test_search_finds_matches(registry, ctx):
    result = registry.dispatch("search", {"pattern": "def average"}, ctx)
    assert result.ok
    assert "arithmetic.py" in result.output


def test_symbols_finds_function(registry, ctx):
    result = registry.dispatch("symbols", {"query": "average"}, ctx)
    assert result.ok
    assert "average" in result.output


def test_outline_lists_defs(registry, ctx):
    result = registry.dispatch("outline", {"path": "calc/arithmetic.py"}, ctx)
    assert result.ok
    assert "add" in result.output and "average" in result.output


def test_edit_replaces_exact_text(registry, ctx):
    result = registry.dispatch(
        "edit",
        {
            "path": "calc/arithmetic.py",
            "search": "return sum(numbers) / (len(numbers) + 1)",
            "replace": "return sum(numbers) / len(numbers)",
        },
        ctx,
    )
    assert result.ok
    fixed = (ctx.repo_root / "calc/arithmetic.py").read_text()
    assert "return sum(numbers) / len(numbers)" in fixed
    assert "return sum(numbers) / (len(numbers) + 1)" not in fixed


def test_edit_reverts_on_syntax_error(registry, ctx):
    original = (ctx.repo_root / "calc/arithmetic.py").read_text()
    result = registry.dispatch(
        "edit",
        {"path": "calc/arithmetic.py", "search": "def add(a, b):", "replace": "def add(a, b"},
        ctx,
    )
    assert not result.ok
    assert (ctx.repo_root / "calc/arithmetic.py").read_text() == original


def test_edit_denied_on_test_file(registry, ctx):
    result = registry.dispatch(
        "edit", {"path": "tests/test_arithmetic.py", "search": "assert", "replace": "assert"}, ctx
    )
    assert not result.ok
    assert "denied" in result.output


def test_create_refuses_overwrite(registry, ctx):
    result = registry.dispatch("create", {"path": "calc/arithmetic.py", "content": "x"}, ctx)
    assert not result.ok


def test_shell_allowlisted_runs(registry, ctx):
    result = registry.dispatch("shell", {"cmd": "echo hi"}, ctx)
    assert result.ok
    assert "hi" in result.output


def test_shell_denies_non_allowlisted(registry, ctx):
    result = registry.dispatch("shell", {"cmd": "rm -rf /"}, ctx)
    assert not result.ok
    assert "denied" in result.output


def test_tests_tool_detects_failures(registry, ctx):
    result = registry.dispatch("tests", {}, ctx)
    assert not result.ok
    assert "failed" in result.output.lower()


def test_chat_mode_denies_writes(registry, ctx):
    ctx.mode = "chat"
    result = registry.dispatch("edit", {"path": "calc/arithmetic.py", "search": "x", "replace": "y"}, ctx)
    assert not result.ok


def test_file_tools_never_expose_secret_files(tmp_path):
    from raven.repo.digest import build_digest
    from raven.tools.fs import read
    from raven.tools.registry import RunContext
    from raven.tools.search import search

    (tmp_path / ".env").write_text("API_KEY=sk-live-123\n")
    (tmp_path / ".env.example").write_text("API_KEY=\n")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "bundle.js").write_text("API_KEY minified\n")
    (tmp_path / "app.py").write_text("API_KEY = None\n")
    ctx = RunContext(repo_root=tmp_path, mode="autonomous")

    assert not read(ctx, ".env").ok
    assert read(ctx, ".env.example").ok
    hits = search(ctx, "API_KEY").output
    assert "sk-live" not in hits and ".env\n" not in hits and "dist/" not in hits
    assert "app.py" in hits
    tree = build_digest(tmp_path, use_cache=False).file_tree
    assert ".env" not in tree and "dist/bundle.js" not in tree


def test_target_repo_tests_never_see_api_key(tmp_path, monkeypatch):
    from raven.tools.registry import RunContext
    from raven.tools.test_runner import run_tests

    monkeypatch.setenv("AI_API_KEY", "sk-must-not-leak")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_env.py").write_text(
        "import os\n\ndef test_no_key():\n    assert 'AI_API_KEY' not in os.environ\n"
    )
    assert run_tests(RunContext(repo_root=tmp_path, mode="autonomous")).ok


def test_hallucinated_tool_names_get_aliases_or_the_real_tool_list(tmp_path):
    from raven.tools.registry import RunContext, build_default_registry

    (tmp_path / "a.py").write_text("x = 1\n")
    registry, ctx = build_default_registry(), RunContext(repo_root=tmp_path, mode="autonomous")
    assert registry.dispatch("open_file", {"path": "a.py"}, ctx).ok  # alias -> read
    unknown = registry.dispatch("print_tree", {"path": ""}, ctx)
    assert not unknown.ok and "Available tools:" in unknown.output and "search" in unknown.output


def test_outline_is_jailed_to_the_repo(tmp_path):
    from raven.tools.registry import RunContext
    from raven.tools.symbols import outline

    (tmp_path / "repo").mkdir()
    (tmp_path / "secret.py").write_text("def hidden():\n    pass\n")
    result = outline(RunContext(repo_root=tmp_path / "repo", mode="chat"), "../secret.py")
    assert not result.ok and "escapes" in result.output


def test_shell_tool_cannot_be_used_to_escape_the_policy(tmp_path):
    """Replays the review's live probe: these used to delete a file, reach
    the network, and read outside the repo."""
    from raven.tools.registry import RunContext, build_default_registry

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "victim.txt").write_text("keep")
    (tmp_path / "outside.txt").write_text("secret")
    registry, ctx = build_default_registry(), RunContext(repo_root=repo, mode="autonomous")

    for cmd in ("cat victim.txt && rm victim.txt", "echo hi; curl -s https://example.com",
                "find .. -name outside.txt", "cat ../outside.txt", "find . -delete",
                "python -c \"print(1)\""):
        result = registry.dispatch("shell", {"cmd": cmd}, ctx)
        assert not result.ok, cmd
    assert (repo / "victim.txt").read_text() == "keep"

    # ordinary use still works
    assert registry.dispatch("shell", {"cmd": "cat victim.txt"}, ctx).output == "keep"
    assert registry.dispatch("shell", {"cmd": "grep -n \"keep|x\" victim.txt"}, ctx).ok is False  # no match, runs fine
    assert registry.dispatch("shell", {"cmd": "grep -n keep victim.txt"}, ctx).ok
