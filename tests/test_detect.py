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
    # none of the preferred names exist: pick the best usable model the key can see.
    # Open-source sizes (qwen3-235b-a22b, qwen3-32b) are stream-only on DashScope: never picked.
    assert choose_model(["qwen-vl-plus", "text-embedding-v4", "qwen3-235b-a22b", "qwen3-32b", "qwen3.9-max",
                         "qwen3.9-flash"], ["qwen3-coder-plus"]) == "qwen3.9-max"
    assert choose_model(["deepseek-reasoner", "deepseek-v5-pro", "deepseek-flash"], []) == "deepseek-v5-pro"
    assert choose_model(["text-embedding-v3", "qwen-vl-max", "qwen3-32b"], []) is None


def test_current_official_model_lists_resolve_to_a_strong_non_thinking_model():
    # the model ids the providers' own docs list as of Sept 2026
    from raven.llm.detect import DEFAULT_PROVIDERS as P
    assert choose_model(["deepseek-flash", "deepseek-v4-pro"], P[0]["models"]) == "deepseek-v4-pro"
    assert choose_model(["qwen3.8-max", "qwen3.8-flash", "qwen-plus", "qwen-turbo", "qwen3-32b"],
                        P[1]["models"]) == "qwen3.8-max"
    assert P[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert all(p["extra_body"] == {"enable_thinking": False} for p in P if p["name"].startswith("qwen"))


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


def _dashscope_like(handler_log, strict_tool_choice=True, need_thinking_off=True):
    def handler(request):
        body = json.loads(request.content)
        handler_log.append(body)
        if need_thinking_off and body.get("enable_thinking") is not False:
            return httpx.Response(400, json={"error": {"message":
                "parameter.enable_thinking must be set to false for non-streaming calls"}})
        if strict_tool_choice and "tool_choice" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: tool_choice"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    return handler


def test_dashscope_quirks_are_handled_without_losing_native_tools():
    from raven.llm.providers import OpenAICompatibleClient

    log = []
    client = OpenAICompatibleClient(base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                                    api_key="k", model="qwen3.8-max")   # no extra_body configured at all
    client._client = httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(_dashscope_like(log)))
    assert client.complete([], tools=[{"type": "function"}]).text == "ok"
    final = log[-1]
    assert final["enable_thinking"] is False and "tools" in final and "tool_choice" not in final
    assert client.native_tools is True


def test_unknown_provider_field_is_dropped_not_fatal():
    from raven.llm.providers import OpenAICompatibleClient

    log = []

    def handler(request):
        body = json.loads(request.content)
        log.append(body)
        if "thinking" in body:
            return httpx.Response(400, json={"error": {"message": "Unrecognized request argument: thinking"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = OpenAICompatibleClient(base_url="https://api.deepseek.com/v1", api_key="k", model="deepseek-chat",
                                    extra_body={"thinking": {"type": "disabled"}})
    client._client = httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(handler))
    assert client.complete([]).text == "ok" and "thinking" not in log[-1]


def test_reasoning_only_reply_is_not_an_empty_answer():
    from raven.llm.providers import OpenAICompatibleClient

    client = OpenAICompatibleClient(base_url="https://api.deepseek.com/v1", api_key="k", model="deepseek-v4-pro")
    client._client = httpx.Client(base_url="https://x/v1", transport=httpx.MockTransport(lambda r: httpx.Response(
        200, json={"choices": [{"message": {"content": None, "reasoning_content": '{"choice": "A"}'}}]})))
    assert client.complete([]).text == '{"choice": "A"}'


def test_explicit_known_endpoint_still_gets_its_quirk_settings(monkeypatch):
    monkeypatch.setenv("RAVEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("RAVEN_MODEL", "qwen-plus")
    monkeypatch.setenv("AI_API_KEY", "sk-x")
    config = resolve_llm(load_config())
    assert config.llm.extra_body == {"enable_thinking": False}


def test_config_yaml_and_code_defaults_agree():
    from raven.llm.detect import DEFAULT_PROVIDERS

    assert load_config().llm.providers == DEFAULT_PROVIDERS
