"""OpenAI-compatible chat-completions client over httpx. This is the only
provider Raven speaks; any endpoint implementing the OpenAI chat-completions
shape (the organiser's endpoint included) works through it.

Tool calling (plan §8.1): when the caller offers tool schemas and
tool_protocol isn't "text", they're sent natively and any tool call the
model makes comes back rendered as a text ```action block — so native and
text protocols share one downstream path. Two recoveries keep a run alive:
- an endpoint that rejects the `tools` field gets it dropped for the rest
  of the session (text protocol from then on);
- a 400 `tool_use_failed` (the model called a tool the request didn't
  offer, or used a namespaced name like "repo.search") carries the
  attempted call in `failed_generation`; it's recovered as the action
  instead of failing the run. Groq + gpt-oss-20b does exactly this."""

from __future__ import annotations

import json
from typing import Callable, Iterable

import httpx

from raven.llm.protocol import CompletionResult, Message, Usage, tool_call_to_action_text


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0,
        seed: int = 7,
        max_output_tokens: int = 2048,
        timeout_s: int = 60,
        tool_protocol: str = "auto",
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.max_output_tokens = max_output_tokens
        # None = not yet known; False once the endpoint rejected `tools`
        self.native_tools: bool | None = False if tool_protocol == "text" else None
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )

    def _payload(self, messages: Iterable[Message], stream: bool, tools: list[dict] | None = None) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
            "seed": self.seed,
            "max_tokens": self.max_output_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    @staticmethod
    def _error_of(resp: httpx.Response) -> dict:
        try:
            err = resp.json().get("error", {})
            return err if isinstance(err, dict) else {}
        except ValueError:
            return {}

    @staticmethod
    def _recover_failed_generation(failed: str) -> str:
        """The attempted tool call from a tool_use_failed error, as an
        action block (or the raw text, for the parser to try)."""
        try:
            data, _ = json.JSONDecoder().raw_decode(failed.strip())
        except (json.JSONDecodeError, ValueError):
            return failed
        if isinstance(data, dict) and data.get("name"):
            return tool_call_to_action_text(data["name"], data.get("arguments", data.get("parameters", {})))
        return failed

    def complete(
        self,
        messages: list[Message],
        stream: bool = False,
        on_token: Callable[[str], None] | None = None,
        tools: list[dict] | None = None,
    ) -> CompletionResult:
        if stream and on_token is not None:
            return self._complete_streaming(messages, on_token)
        send_tools = tools if tools and self.native_tools is not False else None
        resp = self._client.post("/chat/completions", json=self._payload(messages, False, send_tools))
        if resp.status_code == 400:
            err = self._error_of(resp)
            if err.get("code") == "tool_use_failed" and err.get("failed_generation"):
                return CompletionResult(
                    text=self._recover_failed_generation(err["failed_generation"]),
                    raw={"recovered_from": "tool_use_failed"},
                )
            if send_tools and "tool" in str(err.get("message", "")).lower():
                # the endpoint won't take `tools` at all: text protocol from now on
                self.native_tools = False
                resp = self._client.post("/chat/completions", json=self._payload(messages, False))
        resp.raise_for_status()
        if send_tools and self.native_tools is None:
            self.native_tools = True  # the endpoint accepted `tools`
        data = resp.json()
        message = data["choices"][0]["message"]
        text = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            # one action per turn (the executor's contract): the first call
            fn = tool_calls[0].get("function", {})
            text = (text + "\n" if text else "") + tool_call_to_action_text(fn.get("name", ""), fn.get("arguments"))
        usage_raw = data.get("usage", {})
        usage = Usage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
        )
        return CompletionResult(text=text, usage=usage, raw=data)

    def _complete_streaming(
        self, messages: list[Message], on_token: Callable[[str], None]
    ) -> CompletionResult:
        chunks: list[str] = []
        with self._client.stream(
            "POST", "/chat/completions", json=self._payload(messages, stream=True)
        ) as resp:
            if resp.status_code >= 400:
                # Must read the body before it's accessible on the raised
                # exception — httpx streaming responses aren't buffered
                # until .read() is called, and exc.response.text on an
                # unread stream raises its own (misleading) error.
                resp.read()
                resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                event = json.loads(payload)
                delta = event["choices"][0].get("delta", {})
                token = delta.get("content")
                if token:
                    chunks.append(token)
                    on_token(token)
        return CompletionResult(text="".join(chunks))

    def close(self) -> None:
        self._client.close()
