from raven.core.understand import understand
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway

VALID_RESPONSE = """\
```understanding
{
  "task_type": "bug_fix",
  "summary": "average() divides by the wrong count",
  "entities": {"files": ["calc/arithmetic.py"], "symbols": ["average"], "errors": [], "commands": []},
  "acceptance_criteria": ["test_average passes"],
  "verification_strategy": "existing_tests",
  "ambiguities": [],
  "risk": "low"
}
```
"""


def test_understand_parses_valid_response():
    gateway = LLMGateway(FakeClient(scripted_responses=[VALID_RESPONSE]))
    result = understand(gateway, "fix the average() bug in calc/arithmetic.py")
    assert result.task_type == "bug_fix"
    assert "calc/arithmetic.py" in result.entities["files"]
    assert result.verification_strategy == "existing_tests"


def test_understand_falls_back_on_garbage_response():
    gateway = LLMGateway(FakeClient(scripted_responses=["not even close to json"]))
    result = understand(gateway, "fix the ValueError in calc/strings.py")
    assert result.task_type == "bug_fix"  # default
    assert "calc/strings.py" in result.entities["files"]  # from regex pre-pass
    assert "ValueError" in result.entities["errors"]


def test_understand_rejects_invalid_task_type():
    bad = '```understanding\n{"task_type": "not_a_real_type"}\n```'
    gateway = LLMGateway(FakeClient(scripted_responses=[bad]))
    result = understand(gateway, "do something")
    assert result.task_type == "bug_fix"  # falls back to default
