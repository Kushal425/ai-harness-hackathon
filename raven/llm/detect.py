"""Provider + model auto-detection from AI_API_KEY alone.

The evaluation guarantees only one thing: `AI_API_KEY`. The organisers use
DeepSeek and Qwen APIs but haven't named endpoints or models, so when
config.yaml says `base_url: auto` Raven works both out at startup:

  1. which provider accepts the key -- each candidate's `GET /models` (a
     free listing call, no tokens), in config order; keys with an
     unambiguous prefix (OpenRouter `sk-or-`, Groq `gsk_`) go only to
     that provider;
  2. which model to use -- the first of the provider's preferred models
     (config.yaml) that the key can see, else the best-looking chat/coder
     model it lists (never vision/embedding/audio/reasoning-only models).

The choice is printed at startup, so every run records what it used.
RAVEN_BASE_URL / RAVEN_MODEL / RAVEN_PROVIDER override detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

DEFAULT_PROVIDERS = [
    {"name": "deepseek", "base_url": "https://api.deepseek.com/v1",
     "models": ["deepseek-chat", "deepseek-v3", "deepseek-coder"]},
    {"name": "qwen", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen3-coder-plus", "qwen3-coder-flash", "qwen-coder-plus", "qwen-plus", "qwen-max", "qwen-turbo"]},
    {"name": "qwen-cn", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen3-coder-plus", "qwen3-coder-flash", "qwen-coder-plus", "qwen-plus", "qwen-max", "qwen-turbo"]},
    {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1", "key_prefix": "sk-or-",
     "models": ["qwen/qwen3-coder", "deepseek/deepseek-chat-v3.1", "deepseek/deepseek-chat"]},
    {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "key_prefix": "gsk_",
     "models": ["qwen/qwen3-32b", "openai/gpt-oss-20b"]},
]
# never pick these for code work
_EXCLUDE = re.compile(r"(vl|vision|embed|audio|tts|asr|speech|image|ocr|rerank|moderation|guard|omni|"
                      r"reasoner|math|realtime|transcri|whisper|dall|search)", re.I)
_PREFER = [re.compile(p, re.I) for p in (r"coder", r"deepseek-(chat|v\d)", r"qwen3", r"qwen.*(plus|max)",
                                         r"chat", r"instruct")]


class DetectionError(RuntimeError):
    pass


@dataclass
class Detected:
    provider: str
    base_url: str
    model: str
    listed: int = 0          # how many models the key can see there


def choose_model(available: list[str], preferred: list[str]) -> str | None:
    """First preferred model the endpoint lists; else the best-ranked
    usable model it lists; None if nothing usable."""
    ids = set(available)
    for m in preferred:
        if m in ids:
            return m
    usable = [m for m in available if not _EXCLUDE.search(m)]
    if not usable:
        return None
    def rank(m: str):
        return (next((i for i, p in enumerate(_PREFER) if p.search(m)), len(_PREFER)), len(m), m)
    return min(usable, key=rank)


def _candidates(api_key: str, providers: list[dict], forced: str | None) -> list[dict]:
    if forced:
        return [p for p in providers if p["name"] == forced or p["name"].startswith(forced)]
    prefixed = [p for p in providers if p.get("key_prefix") and api_key.startswith(p["key_prefix"])]
    if prefixed:
        return prefixed  # an unambiguous key never goes to another provider
    return [p for p in providers if not p.get("key_prefix")]


def detect(api_key: str, providers: list[dict] | None = None, forced: str | None = None,
           transport=None, timeout: float = 10.0) -> Detected:
    providers = providers or DEFAULT_PROVIDERS
    tried = []
    for p in _candidates(api_key, providers, forced):
        url = p["base_url"].rstrip("/")
        try:
            with httpx.Client(timeout=timeout, transport=transport) as c:
                resp = c.get(f"{url}/models", headers={"Authorization": f"Bearer {api_key}"})
        except httpx.HTTPError as exc:
            tried.append(f"{p['name']}: unreachable ({type(exc).__name__})")
            continue
        if resp.status_code in (401, 403):
            tried.append(f"{p['name']}: key not accepted")
            continue
        if resp.status_code != 200:
            # key may be fine but the listing unavailable: trust the preferred model
            tried.append(f"{p['name']}: /models returned {resp.status_code}")
            if resp.status_code in (404, 405) and p.get("models"):
                return Detected(p["name"], url, p["models"][0])
            continue
        try:
            data = resp.json()
            ids = [m["id"] for m in (data.get("data") or data.get("models") or []) if isinstance(m, dict) and "id" in m]
        except (ValueError, KeyError, AttributeError):
            ids = []
        model = choose_model(ids, p.get("models", [])) if ids else (p.get("models") or [None])[0]
        if model:
            return Detected(p["name"], url, model, len(ids))
        tried.append(f"{p['name']}: key accepted but no usable chat model listed")
    raise DetectionError("could not find a provider that accepts AI_API_KEY (" + "; ".join(tried or ["none tried"]) +
                         "). Set RAVEN_BASE_URL and RAVEN_MODEL explicitly.")
