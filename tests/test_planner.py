from raven.core.planner import make_plan
from raven.core.understand import Understanding
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway

VALID_PLAN = """\
```plan
{"steps": [
  {"action": "locate average()", "tools": ["search"], "check": "found the function"},
  {"action": "fix the off-by-one", "tools": ["edit"], "check": "test_average passes"},
  {"action": "run tests", "tools": ["tests"], "check": "suite is green"}
]}
```
"""


def test_make_plan_parses_steps():
    gateway = LLMGateway(FakeClient(scripted_responses=[VALID_PLAN]))
    understanding = Understanding(task_type="bug_fix", summary="fix average()")
    plan = make_plan(gateway, understanding, digest_summary="python repo")
    assert len(plan.steps) == 3
    assert plan.steps[0].id == 1
    assert "average" in plan.steps[0].action


def test_make_plan_caps_at_seven_steps():
    many_steps = {"steps": [{"action": f"step {i}", "tools": [], "check": ""} for i in range(10)]}
    import json

    text = f"```plan\n{json.dumps(many_steps)}\n```"
    gateway = LLMGateway(FakeClient(scripted_responses=[text]))
    plan = make_plan(gateway, Understanding(), digest_summary="")
    assert len(plan.steps) == 7


def test_make_plan_falls_back_to_single_step_on_garbage():
    gateway = LLMGateway(FakeClient(scripted_responses=["nonsense"]))
    understanding = Understanding(summary="fix the bug")
    plan = make_plan(gateway, understanding, digest_summary="")
    assert len(plan.steps) == 1
    assert plan.steps[0].action == "fix the bug"


def test_plan_as_text_shows_status_marks():
    gateway = LLMGateway(FakeClient(scripted_responses=[VALID_PLAN]))
    plan = make_plan(gateway, Understanding(), digest_summary="")
    plan.steps[0].status = "done"
    text = plan.as_text()
    assert "✓" in text
    assert "·" in text
