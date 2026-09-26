"""Prompt loader (plan §12.3): every prompt Raven sends to the model lives in
prompts/base.yaml (or prompts.tuned.yaml, if the offline evolution loop has
produced one), not hardcoded in Python. This is what lets raven/learn/evolve.py
mutate individual prompt modules and ship a winner without touching code.

Loaded once per process and cached — prompts don't change mid-run."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_PROMPTS_PATH = REPO_ROOT / "prompts" / "base.yaml"

_cache: dict[str, dict[str, str]] = {}


def _prompts_path(repo_root: Path | None) -> Path:
    root = Path(repo_root) if repo_root else REPO_ROOT
    tuned = root / "prompts.tuned.yaml"
    return tuned if tuned.exists() else BASE_PROMPTS_PATH


def load_prompts(repo_root: Path | None = None, use_cache: bool = True) -> dict[str, str]:
    """Returns {module_name: text}. prompts.tuned.yaml at repo_root (or the
    Raven package root if repo_root is None) overrides prompts/base.yaml
    entirely if present -- it's expected to be a full copy with edited
    modules, not a partial patch."""
    path = _prompts_path(repo_root)
    cache_key = str(path)
    if use_cache and cache_key in _cache:
        return _cache[cache_key]

    raw = yaml.safe_load(path.read_text()) or {}
    prompts = {name: (entry.get("text", "") if isinstance(entry, dict) else str(entry)).strip("\n")
               for name, entry in raw.items()}

    if use_cache:
        _cache[cache_key] = prompts
    return prompts


def get_prompt(name: str, repo_root: Path | None = None) -> str:
    prompts = load_prompts(repo_root)
    if name not in prompts:
        raise KeyError(f"unknown prompt module: {name} (checked {_prompts_path(repo_root)})")
    return prompts[name]


def clear_cache() -> None:
    """Test/evolve-loop helper: forces the next load_prompts() to re-read
    from disk instead of returning a cached dict."""
    _cache.clear()
