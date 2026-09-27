"""Provider + model auto-detection from AI_API_KEY alone (DeepSeek / Qwen),
against mocked endpoints shaped like the real ones (401 for a wrong key)."""

from __future__ import annotations

import json

import httpx
import pytest

from raven.config import load_config, resolve_llm
from raven.llm.detect import DEFAULT_PROVIDERS, DetectionError, choose_model, detect


def _net(accepting: dict[str, list[str]], seen: list):
    """Hosts in `accepting` list those models for any key; others 401."""
    def handler(request: httpx.Request):
        seen.append(request.url.host)
        models = accepting.get(request.url.host)
        if models is None:
            return httpx.Response(401, json={"error": {"message": "Incorrect API key provided."}})
        return httpx.Response(200, json={"object": "list", "data": [{"id": m} for m in models]})
    return httpx.MockTransport(handler)


def test_deepseek_key_picks_deepseek_chat_and_never_touches_qwen():
    seen = []
    found = detect("sk-deepseekkey", transport=_net({"api.deepseek.com": ["deepseek-chat", "deepseek-reasoner"]}, seen))
    assert (found.provider, found.model) == ("deepseek", "deepseek-chat")
    assert seen == ["api.deepseek.com"]


def test_qwen_key_falls_through_to_dashscope_and_picks_a_coder_model():
    seen = []
    listing = ["qwen-vl-max", "text-embedding-v3", "qwen-plus", "qwen3-coder-plus"]
    found = detect("sk-dashscopekey", transport=_net({"dashscope-intl.aliyuncs.com": listing}, seen))
    assert (found.provider, found.model) == ("qwen", "qwen3-coder-plus")
    assert seen == ["api.deepseek.com", "dashscope-intl.aliyuncs.com"]


def test_unknown_model_names_are_ranked_not_guessed():
    # none of the preferred names exist: pick the best usable model the key can see
    assert choose_model(["qwen-vl-plus", "text-embedding-v4", "qwen2.5-72b-instruct", "qwen3-235b-a22b"],
                        ["qwen3-coder-plus"]) == "qwen3-235b-a22b"
    assert choose_model(["deepseek-reasoner", "deepseek-v4-chat"], []) == "deepseek-v4-chat"
    assert choose_model(["text-embedding-v3", "qwen-vl-max"], []) is None


def test_prefixed_keys_only_go_to_their_own_provider():
    seen = []
    found = detect("sk-or-v1-abc", transport=_net({"openrouter.ai": ["qwen/qwen3-coder", "x/y"]}, seen))
    assert found.provider == "openrouter" and seen == ["openrouter.ai"]


def test_no_provider_accepts_the_key_gives_an_actionable_error():
    with pytest.raises(DetectionError, match="RAVEN_BASE_URL and RAVEN_MODEL") as exc:
        detect("sk-wrong", transport=_net({}, []))
    assert "deepseek: key not accepted" in str(exc.value) and "qwen: key not accepted" in str(exc.value)


def test_resolve_llm_respects_overrides(monkeypatch):
    for var in ("RAVEN_BASE_URL", "RAVEN_MODEL", "RAVEN_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AI_API_KEY", "sk-anything")
    seen = []
    net = _net({"api.deepseek.com": ["deepseek-chat"], "dashscope-intl.aliyuncs.com": ["qwen-plus"]}, seen)

    config = resolve_llm(load_config(), transport=net)                     # auto: first provider that accepts
    assert (config.llm.base_url, config.llm.model) == ("https://api.deepseek.com/v1", "deepseek-chat")
    assert "auto-detected" in config.llm.detected

    monkeypatch.setenv("RAVEN_PROVIDER", "qwen")                             # forced provider
    seen.clear()
    config = resolve_llm(load_config(), transport=net)
    assert config.llm.model == "qwen-plus" and "api.deepseek.com" not in seen

    monkeypatch.setenv("RAVEN_BASE_URL", "https://proxy.example/v1")         # both explicit: no probing
    monkeypatch.setenv("RAVEN_MODEL", "their-model")
    seen.clear()
    config = resolve_llm(load_config(), transport=net)
    assert (config.llm.model, seen) == ("their-model", [])


def test_config_lists_deepseek_and_qwen_first():
    names = [p["name"] for p in load_config().llm.providers]
    assert names[:2] == ["deepseek", "qwen"] and [p["name"] for p in DEFAULT_PROVIDERS][:2] == names[:2]


# -- provider quirks --------------------------------------------------------------

def _client(handler):
    from raven.llm.providers import OpenAICompatibleClient

    client = OpenAICompatibleClient(base_url="https://api.deepseek.com/v1", api_key="k", model="deepseek-reasoner")
    client._client = httpx.Client(base_url="https://api.deepseek.com/v1", transport=httpx.MockTransport(handler))
    return client


def test_deepseek_function_calling_rejection_falls_back_to_text_protocol():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "tools" in body:
            return httpx.Response(400, json={"error": {"message": "deepseek-reasoner does not support Function Calling"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = _client(handler)
    assert client.complete([], tools=[{"type": "function"}]).text == "ok"
    assert client.native_tools is False and "tools" not in bodies[-1]


def test_endpoint_rejecting_seed_drops_it():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "seed" in body:
            return httpx.Response(400, json={"error": {"message": "Unrecognized request argument supplied: seed"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = _client(handler)
    assert client.complete([]).text == "ok" and client.seed is None
    assert client.complete([]).text == "ok" and len(bodies) == 3  # second call sends no seed at all
