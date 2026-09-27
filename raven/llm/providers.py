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
        extra_body: dict | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.max_output_tokens = max_output_tokens
        # provider-specific request fields (e.g. DeepSeek thinking off, DashScope
        # enable_thinking=false); dropped automatically if an endpoint rejects them
        self.extra_body = dict(extra_body or {})
        self.send_tool_choice = True
        # None = not yet known; False once the endpoint rejected `tools`
        self.native_tools: bool | None = False if tool_protocol == "text" else None
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )

    def _payload(self, messages: Iterable[Message], stream: bool, tools: list[dict] | None = None,
                 temperature: float | None = None) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature if temperature is None else temperature,

            "max_tokens": self.max_output_tokens,
            "stream": stream,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        if tools:
            payload["tools"] = tools
            if self.send_tool_choice:
                payload["tool_choice"] = "auto"
        payload.update(self.extra_body)
        return payload

    def _adapt_to_400(self, message: str, sent_tools: bool) -> bool:
        """Adjust the request for a known endpoint quirk named in a 400;
        True = retry. Each adjustment happens at most once per session."""
        if "enable_thinking" in message and self.extra_body.get("enable_thinking") is not False:
            self.extra_body["enable_thinking"] = False  # DashScope thinking models, non-streaming
            return True
        rejected = [k for k in self.extra_body if k.lower() in message]
        if rejected:
            for k in rejected:
                self.extra_body.pop(k)  # the endpoint doesn't know this provider-specific field
            return True
        if sent_tools and "tool_choice" in message and self.send_tool_choice:
            self.send_tool_choice = False  # keep native tools, just without tool_choice
            return True
        return False

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
        temperature: float | None = None,
    ) -> CompletionResult:
        if stream and on_token is not None:
            return self._complete_streaming(messages, on_token)
        send_tools = tools if tools and self.native_tools is not False else None
        resp = self._client.post("/chat/completions", json=self._payload(messages, False, send_tools, temperature))
        if resp.status_code == 400:
            err = self._error_of(resp)
            if err.get("code") == "tool_use_failed" and err.get("failed_generation"):
                return CompletionResult(
                    text=self._recover_failed_generation(err["failed_generation"]),
                    raw={"recovered_from": "tool_use_failed"},
                )
            message = str(err.get("message", "")).lower()
            retry = self._adapt_to_400(message, bool(send_tools))
            if retry:
                return self.complete(messages, stream=False, on_token=None, tools=tools, temperature=temperature)
            if "seed" in message and self.seed is not None:
                # an endpoint that rejects the seed parameter: drop it for the session
                self.seed = None
                resp = self._client.post("/chat/completions", json=self._payload(messages, False, send_tools, temperature))
                if resp.status_code != 400:
                    resp.raise_for_status()
                    return self._parse(resp, send_tools)
                err = self._error_of(resp)
                message = str(err.get("message", "")).lower()
            if send_tools and ("tool" in message or "function" in message):
                # DeepSeek's reasoner answers "does not support Function Calling"
                # the endpoint won't take `tools` at all: text protocol from now on
                self.native_tools = False
                resp = self._client.post("/chat/completions", json=self._payload(messages, False, None, temperature))
        resp.raise_for_status()
        return self._parse(resp, send_tools)

    def _parse(self, resp, send_tools) -> CompletionResult:
        if send_tools and self.native_tools is None:
            self.native_tools = True  # the endpoint accepted `tools`
        data = resp.json()
        message = data["choices"][0]["message"]
        text = message.get("content") or ""
        if not text.strip() and not message.get("tool_calls") and message.get("reasoning_content"):
            # a thinking model that put everything in its reasoning: better than an empty reply
            text = message["reasoning_content"]
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
