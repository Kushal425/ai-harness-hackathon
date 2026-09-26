import pytest


@pytest.fixture(autouse=True)
def _no_real_github_credentials(tmp_path_factory, monkeypatch):
    """Tests never see the developer's real GitHub login: no env tokens, no
    saved token (isolated config dir), no GitHub CLI fallback."""
    for var in ("GITHUB_TOKEN", "GH_TOKEN", "RAVEN_GITHUB_CLIENT_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    monkeypatch.setenv("RAVEN_NO_GH_CLI", "1")
