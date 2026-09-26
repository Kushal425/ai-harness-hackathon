"""Loads Raven's configuration: config.tuned.yaml (if present) or config.yaml,
with select fields overridable by environment variables. The API key is read
only from AI_API_KEY, never from config or code (plan §0.3)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class LLMConfig:
    provider: str = "openai_compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0
    seed: int = 7
    context_window: int = 32000
    max_output_tokens: int = 2048
    tool_protocol: str = "auto"
    request_timeout_s: int = 60
    max_retries: int = 5
    api_key: str = ""


@dataclass
class RavenConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    raw: dict = field(default_factory=dict)

    def flag(self, section: str, key: str, default: bool) -> bool:
        """A boolean switch from config.yaml, e.g. flag("verify", "reproduce", True)."""
        return bool((self.raw.get(section) or {}).get(key, default))


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_config(config_path: Path | None = None) -> RavenConfig:
    """Loads config.tuned.yaml if present, else config.yaml. Env vars
    RAVEN_MODEL and RAVEN_BASE_URL override the corresponding llm fields.
    AI_API_KEY is always read from the environment."""
    if config_path is None:
        tuned = REPO_ROOT / "config.tuned.yaml"
        config_path = tuned if tuned.exists() else REPO_ROOT / "config.yaml"

    raw = _load_yaml(config_path)
    llm_raw = raw.get("llm", {})

    llm = LLMConfig(
        provider=llm_raw.get("provider", LLMConfig.provider),
        base_url=os.environ.get("RAVEN_BASE_URL") or llm_raw.get("base_url", LLMConfig.base_url),
        model=os.environ.get("RAVEN_MODEL") or llm_raw.get("model", LLMConfig.model),
        temperature=llm_raw.get("temperature", LLMConfig.temperature),
        seed=llm_raw.get("seed", LLMConfig.seed),
        context_window=llm_raw.get("context_window", LLMConfig.context_window),
        max_output_tokens=llm_raw.get("max_output_tokens", LLMConfig.max_output_tokens),
        tool_protocol=llm_raw.get("tool_protocol", LLMConfig.tool_protocol),
        request_timeout_s=llm_raw.get("request_timeout_s", LLMConfig.request_timeout_s),
        max_retries=llm_raw.get("max_retries", LLMConfig.max_retries),
        api_key=os.environ.get("AI_API_KEY", ""),
    )
    return RavenConfig(llm=llm, raw=raw)
