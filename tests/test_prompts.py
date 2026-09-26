from raven.prompts import clear_cache, get_prompt, load_prompts

EXPECTED_KEYS = {
    "protocol_instructions", "answer_mode_instructions", "chat_system",
    "understand_system", "plan_system", "reviewer_system",
    "debugger_hypotheses", "debugger_verdict", "explorer_goal_prefix",
}


def test_base_prompts_has_all_expected_modules():
    prompts = load_prompts()
    assert EXPECTED_KEYS <= prompts.keys()
    for key in EXPECTED_KEYS:
        assert prompts[key].strip(), f"{key} is empty"


def test_get_prompt_returns_text():
    text = get_prompt("plan_system")
    assert "```plan" in text


def test_get_prompt_raises_on_unknown_module():
    try:
        get_prompt("does_not_exist")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_prompts_tuned_yaml_overrides_base(tmp_path):
    clear_cache()
    (tmp_path / "prompts.tuned.yaml").write_text(
        "plan_system:\n  version: 2\n  text: |\n    TUNED PLAN PROMPT\n"
    )
    prompts = load_prompts(repo_root=tmp_path, use_cache=False)
    assert prompts["plan_system"] == "TUNED PLAN PROMPT"
    clear_cache()


def test_no_tuned_file_falls_back_to_base(tmp_path):
    clear_cache()
    prompts = load_prompts(repo_root=tmp_path, use_cache=False)
    assert "```plan" in prompts["plan_system"]
    clear_cache()
