from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway
from raven.llm.protocol import Message


def test_gateway_complete_returns_scripted_text():
    client = FakeClient(scripted_responses=["hi there"])
    gateway = LLMGateway(client)
    result = gateway.complete([Message(role="user", content="hello")])
    assert result.text == "hi there"
    assert gateway.stats.calls == 1


def test_gateway_streaming_calls_on_token():
    client = FakeClient(scripted_responses=["a b c"])
    gateway = LLMGateway(client)
    tokens = []
    result = gateway.complete(
        [Message(role="user", content="hi")], stream=True, on_token=tokens.append
    )
    assert "".join(tokens).strip() == "a b c"
    assert result.text == "a b c"


def test_gateway_accumulates_usage():
    client = FakeClient(scripted_responses=["one", "two"])
    gateway = LLMGateway(client)
    gateway.complete([Message(role="user", content="x" * 20)])
    gateway.complete([Message(role="user", content="y" * 20)])
    assert gateway.stats.total_tokens > 0
    assert gateway.stats.calls == 2


def test_probe_tool_calling_returns_bool_on_success():
    gateway = LLMGateway(FakeClient(scripted_responses=["ok"]))
    assert gateway.probe_tool_calling() is True


def test_probe_tool_calling_degrades_gracefully_on_failure():
    class BrokenClient:
        def complete(self, *a, **k):
            raise RuntimeError("boom")

    gateway = LLMGateway(BrokenClient())
    assert gateway.probe_tool_calling() is False
