import httpx
import pytest

from raven.llm.gateway import LLMGateway
from raven.llm.protocol import Message
from raven.llm.providers import OpenAICompatibleClient


def _client_with_transport(transport: httpx.MockTransport) -> OpenAICompatibleClient:
    client = OpenAICompatibleClient(
        base_url="https://fake.example/v1", api_key="bad-key", model="does-not-exist"
    )
    client._client = httpx.Client(
        base_url=client._client.base_url, headers=client._client.headers, transport=transport
    )
    return client


def test_streaming_http_error_surfaces_body_not_streaming_access_error():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    client = _client_with_transport(httpx.MockTransport(handler))
    gateway = LLMGateway(client, max_retries=0)

    with pytest.raises(RuntimeError) as exc_info:
        gateway.complete([Message(role="user", content="hi")], stream=True, on_token=lambda t: None)

    message = str(exc_info.value)
    assert "Attempted to access streaming response content" not in message
    assert "401" in message
    assert "invalid api key" in message


def test_non_streaming_http_error_surfaces_body():
    def handler(request):
        return httpx.Response(404, json={"error": {"message": "model not found"}})

    client = _client_with_transport(httpx.MockTransport(handler))
    gateway = LLMGateway(client, max_retries=0)

    with pytest.raises(RuntimeError) as exc_info:
        gateway.complete([Message(role="user", content="hi")], stream=False)

    message = str(exc_info.value)
    assert "404" in message
    assert "model not found" in message


def test_4xx_fails_fast_without_retrying():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    client = _client_with_transport(httpx.MockTransport(handler))
    gateway = LLMGateway(client, max_retries=5)

    with pytest.raises(RuntimeError):
        gateway.complete([Message(role="user", content="hi")], stream=False)

    assert len(calls) == 1  # no retries burned on a non-retryable 4xx


# The exact rejection Groq returned for gpt-oss-20b in a real run: the
# model made a native tool call (with a namespaced name) and the endpoint
# 400'd the whole response, which used to abort the run with 0 tool calls.
GROQ_TOOL_USE_FAILED = {
    "error": {
        "message": "Tool choice is none, but model called a tool",
        "type": "invalid_request_error",
        "code": "tool_use_failed",
        "failed_generation": '{"name": "repo.search", "arguments": {"path": "", "pattern": "raven", "regex": false}\n}',
    }
}


def test_tool_use_failed_400_is_recovered_as_the_attempted_action():
    from raven.core.protocol import parse_action

    client = _client_with_transport(httpx.MockTransport(lambda r: httpx.Response(400, json=GROQ_TOOL_USE_FAILED)))
    result = LLMGateway(client, max_retries=0).complete([Message(role="user", content="hi")])

    action = parse_action(result.text)
    assert action.tool == "search"  # "repo.search" -> Raven's tool name
    assert action.args["pattern"] == "raven"


def test_native_tool_call_response_becomes_an_action_block():
    import json

    from raven.core.protocol import parse_action

    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": None, "tool_calls": [
                {"type": "function", "function": {"name": "read", "arguments": '{"path": "README.md"}'}},
            ]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    client = _client_with_transport(httpx.MockTransport(handler))
    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
    result = LLMGateway(client).complete([Message(role="user", content="hi")], tools=tools)

    assert sent[0]["tools"] == tools and sent[0]["tool_choice"] == "auto"
    assert parse_action(result.text).args == {"path": "README.md"}
    assert client.native_tools is True


def test_endpoint_that_rejects_tools_falls_back_to_text_protocol():
    import json

    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "tools" in body:
            return httpx.Response(400, json={"error": {"message": "tools are not supported for this model"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = _client_with_transport(httpx.MockTransport(handler))
    gateway = LLMGateway(client)
    tools = [{"type": "function", "function": {"name": "read"}}]
    assert gateway.complete([Message(role="user", content="a")], tools=tools).text == "ok"
    assert gateway.complete([Message(role="user", content="b")], tools=tools).text == "ok"

    assert client.native_tools is False
    assert [("tools" in b) for b in bodies] == [True, False, False]  # probed once, then text only


def test_tool_protocol_text_never_sends_tools():
    import json

    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = OpenAICompatibleClient(base_url="https://fake.example/v1", api_key="k", model="m", tool_protocol="text")
    client._client = httpx.Client(base_url="https://fake.example/v1", transport=httpx.MockTransport(handler))
    LLMGateway(client).complete([Message(role="user", content="a")], tools=[{"type": "function"}])
    assert "tools" not in bodies[0]


def test_autonomous_run_survives_groq_tool_use_failed(tmp_path):
    """The failing real run, end to end: the first model turn is the Groq
    400; the run must carry on from the recovered action and resolve."""
    import json
    import shutil
    from pathlib import Path

    from raven.core.orchestrator import run_orchestrator

    repo = tmp_path / "repo"
    shutil.copytree(Path(__file__).parent / "fixtures" / "toy_repo", repo)
    turns = iter([
        httpx.Response(400, json=GROQ_TOOL_USE_FAILED),
        {"name": "edit", "arguments": {"path": "calc/arithmetic.py",
                                       "search": "return sum(numbers) / (len(numbers) + 1)",
                                       "replace": "return sum(numbers) / len(numbers)"}},
        {"name": "done", "arguments": {"summary": "fixed average()"}},
    ])

    def handler(request):
        body = json.loads(request.content)
        if "tools" not in body:  # lesson extraction / reflection: plain text calls
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
        nxt = next(turns)
        if isinstance(nxt, httpx.Response):
            return nxt
        call = {"type": "function", "function": {"name": nxt["name"], "arguments": json.dumps(nxt["arguments"])}}
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [call]}}]})

    client = _client_with_transport(httpx.MockTransport(handler))
    result = run_orchestrator(LLMGateway(client, max_retries=0), repo, "fix the failing average() test",
                              reproduce=False)

    assert result.accepted, result.reason
    assert result.executor_result.tool_calls == 2  # the recovered search, then the edit


def test_native_call_to_a_tool_named_json_wrapping_the_action_is_unwrapped():
    # gpt-oss quirk seen live: a call to "json" whose arguments are Raven's action
    from raven.core.protocol import parse_action
    from raven.llm.protocol import tool_call_to_action_text

    text = tool_call_to_action_text("json", '{"tool": "done", "args": {"summary": "A harness is ..."}}')
    action = parse_action(text)
    assert action.tool == "done" and action.args == {"summary": "A harness is ..."}
    # a real tool whose args merely include a "name" field is left alone
    assert parse_action(tool_call_to_action_text("symbols", {"name": "average"})).tool == "symbols"
