"""End to end over REAL HTTP against local emulators of the DeepSeek and
Qwen (DashScope) APIs, reproducing their documented quirks strictly:

  DeepSeek:  model ids deepseek-flash / deepseek-v4-pro; thinking mode that
             leaves `content` empty unless disabled; this emulator also
             rejects the `seed` parameter (the real docs don't list it).
  DashScope: non-streaming calls to thinking models fail with
             "parameter.enable_thinking must be set to false for
             non-streaming calls"; `tool_choice` is rejected (undocumented);
             only its own key is accepted.

Raven is run through its real CLI entry point with only AI_API_KEY set:
provider + model detection, the quirk fallbacks, and the whole Crux run
must work, with the model's replies scripted (the Crux demo).
"""

from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import raven.cli as cli
from raven.config import load_config

DEMO = Path(__file__).parent / "fixtures" / "crux_demo"


class _Emulator:
    def __init__(self, kind: str, key: str, models: list[str], script: list[str]):
        self.kind, self.key, self.models, self.script = kind, key, models, list(script)
        self.bodies: list[dict] = []
        emu = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authed(self):
                if self.headers.get("Authorization") != f"Bearer {emu.key}":
                    self._send(401, {"error": {"message": "Incorrect API key provided.", "type": "invalid_request_error"}})
                    return False
                return True

            def do_GET(self):
                if not self._authed():
                    return
                if self.path.endswith("/models"):
                    self._send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in emu.models]})
                else:
                    self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                if not self._authed():
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                emu.bodies.append(body)
                if body.get("model") not in emu.models:
                    return self._send(400, {"error": {"message": f"Model Not Exist: {body.get('model')}"}})
                if emu.kind == "deepseek":
                    if "seed" in body:
                        return self._send(400, {"error": {"message": "Unrecognized request argument supplied: seed"}})
                    thinking_off = (body.get("thinking") or {}).get("type") == "disabled"
                else:
                    if body.get("enable_thinking") is not False:
                        return self._send(400, {"error": {"message":
                            "<400> InternalError.Algo.InvalidParameter: parameter.enable_thinking must be set to "
                            "false for non-streaming calls"}})
                    if "tool_choice" in body:
                        return self._send(400, {"error": {"message": "Unsupported parameter: tool_choice"}})
                    thinking_off = True
                reply = emu.script.pop(0) if emu.script else "{}"
                message = ({"role": "assistant", "content": reply} if thinking_off else
                           {"role": "assistant", "content": None, "reasoning_content": "(thinking...)"})
                self._send(200, {"id": "x", "object": "chat.completion", "choices": [{"index": 0, "message": message}],
                                 "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def _script():
    from tests.test_crux import CAND_A, CAND_B, CAND_C, LOCALIZE, PROBE, VERDICT
    return [LOCALIZE, PROBE, CAND_A, CAND_B, CAND_C, VERDICT]


@pytest.fixture
def providers(monkeypatch):
    """Both emulators, wired in as Raven's provider list (same order and
    settings as config.yaml, only the URLs point at localhost)."""
    ds = _Emulator("deepseek", "sk-deepseek-test", ["deepseek-flash", "deepseek-v4-pro"], _script())
    qw = _Emulator("dashscope", "sk-qwen-test", ["qwen3.8-max", "qwen3.8-flash", "qwen-plus", "qwen3-32b",
                                                 "qwen-vl-max", "text-embedding-v4"], _script())
    real = load_config

    def patched_load_config(*a, **k):
        config = real(*a, **k)
        by_name = {p["name"]: p for p in config.llm.providers}
        config.llm.providers = [{**by_name["deepseek"], "base_url": ds.url}, {**by_name["qwen"], "base_url": qw.url}]
        kw = config.run_kwargs

        def sequential():  # scripted replies are consumed in order
            out = kw()
            out["settings"].parallel_calls = False
            out["settings"].lessons = False
            return out
        config.run_kwargs = sequential
        return config

    monkeypatch.setattr(cli, "load_config", patched_load_config)
    for var in ("RAVEN_BASE_URL", "RAVEN_MODEL", "RAVEN_PROVIDER", "RAVEN_REPO", "RAVEN_ISSUE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False, "read": lambda self: ""})())
    yield ds, qw
    ds.close()
    qw.close()


def _run(tmp_path, capsys):
    from tests.test_crux import ISSUE

    repo = tmp_path / "repo"
    shutil.copytree(DEMO, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    code = cli.main(["--repo", str(repo), "--issue", ISSUE])
    out = capsys.readouterr()
    return code, out.out + out.err, repo


def test_deepseek_key_end_to_end(providers, tmp_path, capsys, monkeypatch):
    ds, qw = providers
    monkeypatch.setenv("AI_API_KEY", "sk-deepseek-test")
    code, out, repo = _run(tmp_path, capsys)

    assert "model: deepseek-v4-pro via 127.0.0.1" in out and "provider deepseek" in out
    assert code == 0 and "accepted: True" in out, out
    chats = [b for b in ds.bodies if "messages" in b]
    assert chats and all(b["thinking"] == {"type": "disabled"} for b in chats)   # never an empty thinking-only reply
    assert "seed" in chats[0] and all("seed" not in b for b in chats[1:])         # rejected once, then dropped
    assert qw.bodies == []                                                         # the Qwen endpoint never saw a chat call
    assert "range(0, len(items), size)" in (repo / "listkit" / "chunks.py").read_text()


def test_qwen_key_end_to_end(providers, tmp_path, capsys, monkeypatch):
    ds, qw = providers
    monkeypatch.setenv("AI_API_KEY", "sk-qwen-test")
    code, out, repo = _run(tmp_path, capsys)

    assert "model: qwen3.8-max via 127.0.0.1" in out and "provider qwen" in out     # not the stream-only qwen3-32b
    assert code == 0 and "accepted: True" in out, out
    chats = [b for b in qw.bodies if "messages" in b]
    assert chats and all(b.get("enable_thinking") is False for b in chats)
    assert ds.bodies == []                                                          # DeepSeek only saw the /models probe
    assert "range(0, len(items), size)" in (repo / "listkit" / "chunks.py").read_text()


def test_a_key_neither_accepts_stops_before_any_work(providers, tmp_path, capsys, monkeypatch):
    ds, qw = providers
    monkeypatch.setenv("AI_API_KEY", "sk-someone-else")
    code, out, repo = _run(tmp_path, capsys)
    assert code == 2 and "could not find a provider that accepts AI_API_KEY" in out
    assert ds.bodies == [] and qw.bodies == []
