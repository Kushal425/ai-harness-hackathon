import os

from raven.config import load_config


def test_load_config_reads_defaults_from_config_yaml():
    config = load_config()
    assert config.llm.model
    assert config.llm.base_url.startswith("http")


def test_env_overrides_model_and_base_url(monkeypatch):
    monkeypatch.setenv("RAVEN_MODEL", "test-model")
    monkeypatch.setenv("RAVEN_BASE_URL", "http://localhost:9999/v1")
    config = load_config()
    assert config.llm.model == "test-model"
    assert config.llm.base_url == "http://localhost:9999/v1"


def test_api_key_read_only_from_environment(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "secret-123")
    config = load_config()
    assert config.llm.api_key == "secret-123"
