from raven.config import load_config
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway
from raven.ui.repl import run_piped


def test_run_piped_prints_result_block(capsys):
    config = load_config()
    gateway = LLMGateway(FakeClient(scripted_responses=["42"]))
    exit_code = run_piped(config, gateway, "what is the answer?")
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "===== RAVEN RESULT =====" in out
    assert "42" in out
    assert "===== END =====" in out


def test_run_piped_handles_client_error_gracefully(capsys):
    class BrokenClient:
        def complete(self, *a, **k):
            raise RuntimeError("network down")

    config = load_config()
    gateway = LLMGateway(BrokenClient(), max_retries=0)
    exit_code = run_piped(config, gateway, "hello")
    assert exit_code == 1
    assert "[error]" in capsys.readouterr().out
