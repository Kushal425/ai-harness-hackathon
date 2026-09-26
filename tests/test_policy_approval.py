from raven.tools.policy import PolicyDecision, check_policy


def test_plan_mode_edit_denied_with_no_approval_channel():
    assert check_policy("plan", "edit", "write", {"path": "x.py"}) == PolicyDecision.DENY


def test_plan_mode_edit_allowed_when_approved():
    assert check_policy("plan", "edit", "write", {"path": "x.py"}, approve_fn=lambda d, a: True) == PolicyDecision.ALLOW


def test_plan_mode_edit_denied_when_rejected():
    assert check_policy("plan", "edit", "write", {"path": "x.py"}, approve_fn=lambda d, a: False) == PolicyDecision.DENY


def test_act_mode_edit_always_allowed_without_asking():
    # Act mode's edit/create path never calls approve_fn — it's a static
    # ALLOW, same as Phase 1. A "deny everything" approve_fn must have no effect.
    assert check_policy("act", "edit", "write", {"path": "x.py"}, approve_fn=lambda d, a: False) == PolicyDecision.ALLOW


def test_act_mode_non_allowlisted_shell_asks():
    assert check_policy("act", "shell", "write", {"cmd": "rm -rf /"}, approve_fn=lambda d, a: True) == PolicyDecision.ALLOW
    assert check_policy("act", "shell", "write", {"cmd": "rm -rf /"}, approve_fn=lambda d, a: False) == PolicyDecision.DENY
    assert check_policy("act", "shell", "write", {"cmd": "rm -rf /"}) == PolicyDecision.DENY  # no channel: Phase 1 default


def test_chat_mode_denies_shell_even_with_approval_channel():
    # Chat is read-only by design (plan §3) — it denies before ever
    # reaching the ask logic, regardless of approve_fn.
    assert check_policy("chat", "shell", "write", {"cmd": "ls"}) == PolicyDecision.DENY
    assert check_policy("chat", "shell", "write", {"cmd": "ls"}, approve_fn=lambda d, a: True) == PolicyDecision.DENY


def test_plan_mode_allowlisted_shell_asks():
    assert check_policy("plan", "shell", "write", {"cmd": "ls"}) == PolicyDecision.DENY
    assert check_policy("plan", "shell", "write", {"cmd": "ls"}, approve_fn=lambda d, a: True) == PolicyDecision.ALLOW


def test_autonomous_mode_never_asks_even_with_approval_channel():
    approve_always = lambda d, a: True
    deny_always = lambda d, a: False
    assert check_policy("autonomous", "shell", "write", {"cmd": "rm -rf /"}, approve_fn=approve_always) == PolicyDecision.DENY
    assert check_policy("autonomous", "edit", "write", {"path": "x.py"}, approve_fn=deny_always) == PolicyDecision.ALLOW


def test_broken_approve_fn_degrades_to_default_instead_of_raising():
    def broken(description, args):
        raise RuntimeError("boom")

    assert check_policy("plan", "edit", "write", {"path": "x.py"}, approve_fn=broken) == PolicyDecision.DENY


def test_editing_existing_test_file_denied_even_when_approved():
    approve_always = lambda d, a: True
    assert check_policy("act", "edit", "write", {"path": "tests/test_x.py"}, approve_fn=approve_always) == PolicyDecision.DENY
