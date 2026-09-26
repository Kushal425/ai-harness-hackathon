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
