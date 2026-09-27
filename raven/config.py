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
    base_url: str = "auto"      # "auto": detected from AI_API_KEY (raven/llm/detect.py)
    model: str = "auto"
    temperature: float = 0
    seed: int = 7
    max_output_tokens: int = 2048
    tool_protocol: str = "auto"
    request_timeout_s: int = 60
    max_retries: int = 5
    api_key: str = ""
    providers: list = field(default_factory=list)  # candidates for auto-detection, in order
    detected: str = ""                              # "deepseek-v4-pro via api.deepseek.com (auto-detected)"
    extra_body: dict = field(default_factory=dict)  # provider-specific request fields (e.g. thinking off)


@dataclass
class RunSettings:
    """Per-run knobs from config.yaml that the orchestrator/executor honour
    (defaults = config.yaml's shipped values)."""
    stall_turns: int = 4
    identical_failures_for_debugger: int = 3
    delegate_min_reads: int = 4
    budget_tokens: int | None = None
    budget_seconds: float | None = None
    trace: bool = True
    behavior_diff: bool = True
    lessons: bool = True
    in_run_reflection: bool = True
    max_lessons_pinned: int = 3
    candidates: int = 3          # crux: first-round candidate fixes
    max_candidates: int = 5      # crux: cap across rounds
    parallel_calls: bool = True  # crux: candidate calls run concurrently


@dataclass
class RavenConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    raw: dict = field(default_factory=dict)

    def run_kwargs(self) -> dict:
        """Keyword arguments for run_orchestrator, from config.yaml."""
        sec = lambda name: self.raw.get(name) or {}  # noqa: E731
        ex, ctx, rec, ver, learn, bud = (
            sec("executor"), sec("context"), sec("recovery"), sec("verify"), sec("learning"), sec("budgets"),
        )
        return {
            "max_iterations": int(ex.get("max_iterations", 30)),
            "max_replans": int(ex.get("max_replans", 2)),
            "half_life": int(ctx.get("half_life", 3)),
            "reproduce": bool(ver.get("reproduce", True)),
            "settings": RunSettings(
                stall_turns=int(rec.get("stall_turns", 4)),
                identical_failures_for_debugger=int(rec.get("identical_failures_for_debugger", 3)),
                delegate_min_reads=int(ex.get("delegate_min_reads", 4)),
                budget_tokens=bud.get("total_tokens"),
                budget_seconds=bud.get("wall_clock_s"),
                trace=bool(ver.get("trace", True)),
                behavior_diff=bool(ver.get("behavior_diff", True)),
                lessons=bool(learn.get("lessons", True)),
                in_run_reflection=bool(learn.get("in_run_reflection", True)),
                max_lessons_pinned=int(learn.get("max_lessons_pinned", 3)),
                candidates=int(ex.get("candidates", 3)),
                max_candidates=int(ex.get("max_candidates", 5)),
                parallel_calls=bool(ex.get("parallel_calls", True)),
            ),
        }



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
        max_output_tokens=llm_raw.get("max_output_tokens", LLMConfig.max_output_tokens),
        tool_protocol=llm_raw.get("tool_protocol", LLMConfig.tool_protocol),
        request_timeout_s=llm_raw.get("request_timeout_s", LLMConfig.request_timeout_s),
        max_retries=llm_raw.get("max_retries", LLMConfig.max_retries),
        api_key=os.environ.get("AI_API_KEY", ""),
        providers=list(llm_raw.get("providers") or []),
    )
    return RavenConfig(llm=llm, raw=raw)


def resolve_llm(config: RavenConfig, transport=None) -> RavenConfig:
    """Fill in `auto` base_url/model by probing the key (see detect.py).
    Explicit values (config or RAVEN_BASE_URL/RAVEN_MODEL) are left alone.
    Raises DetectionError when no provider accepts the key."""
    from urllib.parse import urlparse

    from raven.llm.detect import DEFAULT_PROVIDERS, detect

    llm = config.llm
    if llm.base_url != "auto" and not llm.extra_body:
        known = next((p for p in (llm.providers or DEFAULT_PROVIDERS)
                      if p["base_url"].rstrip("/") == llm.base_url.rstrip("/")), None)
        if known:
            llm.extra_body = dict(known.get("extra_body") or {})
    if not llm.api_key or (llm.base_url != "auto" and llm.model != "auto"):
        return config
    providers = llm.providers or DEFAULT_PROVIDERS
    if llm.base_url != "auto":  # endpoint given, model not: choose from what it lists
        providers = [{"name": "configured", "base_url": llm.base_url, "models": []}]
    found = detect(llm.api_key, providers, forced=os.environ.get("RAVEN_PROVIDER") or None, transport=transport)
    if llm.model != "auto":
        found.model = llm.model
    llm.base_url, llm.model = found.base_url, found.model
    llm.extra_body = found.extra_body
    llm.detected = f"{found.model} via {urlparse(found.base_url).netloc} (auto-detected, provider {found.provider})"
    return config
