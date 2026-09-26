import shutil
from pathlib import Path

from raven.core.orchestrator import run_orchestrator
from raven.learn.extract_lessons import extract_and_store_lesson
from raven.learn.reflect import RunReflection, reflect_on_failure
from raven.llm.fake import FakeClient
from raven.llm.gateway import LLMGateway
from raven.memory import lessons as lessons_mod
from raven.memory.lessons import Lesson, add_lesson, format_lesson, retrieve_combined, retrieve_lessons
from raven.memory.project import project_memory_path, read_project_memory, record_fact

TOY_REPO = Path(__file__).parent / "fixtures" / "toy_repo"


# -- raven/memory/lessons.py --------------------------------------------------

def test_add_lesson_then_retrieve_by_query(tmp_path):
    store = tmp_path / "lessons.jsonl"
    add_lesson(store, Lesson(trigger="off-by-one in average()", insight="denominator wrong", action="check +1/-1"))
    found = retrieve_lessons(store, "average() off-by-one bug", k=3)
    assert len(found) == 1
    assert found[0]["insight"] == "denominator wrong"


def test_add_lesson_deduplicates_similar_entries(tmp_path):
    store = tmp_path / "lessons.jsonl"
    add_lesson(store, Lesson(trigger="off-by-one in average() denominator", insight="wrong denominator count"))
    added_again = add_lesson(store, Lesson(trigger="off-by-one in average() denominator", insight="wrong denominator count"))
    assert added_again is False
    stored = lessons_mod.list_lessons(store)
    assert len(stored) == 1
    assert stored[0]["confirmations"] == 2


def test_add_lesson_keeps_dissimilar_entries_separate(tmp_path):
    store = tmp_path / "lessons.jsonl"
    add_lesson(store, Lesson(trigger="average() off-by-one", insight="denominator wrong"))
    add_lesson(store, Lesson(trigger="palindrome check reversed wrong", insight="compares string to itself"))
    assert len(lessons_mod.list_lessons(store)) == 2


def test_retrieve_lessons_ranks_by_word_overlap(tmp_path):
    store = tmp_path / "lessons.jsonl"
    add_lesson(store, Lesson(trigger="palindrome bug", insight="compares string to itself instead of reversed"))
    add_lesson(store, Lesson(trigger="average bug", insight="off-by-one in denominator of average function"))
    top = retrieve_lessons(store, "average function has an off-by-one denominator bug", k=1)
    assert top[0]["trigger"] == "average bug"


def test_retrieve_combined_merges_repo_and_global_scope(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(lessons_mod, "GLOBAL_LESSONS_PATH", tmp_path / "global.jsonl")
    add_lesson(lessons_mod.repo_lessons_path(repo_root), Lesson(trigger="repo thing", insight="repo insight about widgets"))
    add_lesson(lessons_mod.GLOBAL_LESSONS_PATH, Lesson(trigger="global thing", insight="global insight about widgets"))
    found = retrieve_combined(repo_root, "widgets", k=5)
    assert len(found) == 2


def test_format_lesson_includes_action_when_present():
    text = format_lesson({"insight": "X happens", "action": "do Y"})
    assert text == "X happens -> do Y"
    assert format_lesson({"insight": "X happens"}) == "X happens"


# -- raven/memory/project.py --------------------------------------------------

def test_record_fact_appends_once(tmp_path):
    assert record_fact(tmp_path, "test command: pytest") is True
    assert record_fact(tmp_path, "test command: pytest") is False  # already present
    content = read_project_memory(tmp_path)
    assert content.count("test command: pytest") == 1


def test_project_memory_path_under_raven_dir(tmp_path):
    assert project_memory_path(tmp_path) == tmp_path / ".raven" / "memory" / "project.md"


# -- raven/learn/reflect.py ---------------------------------------------------

def test_reflect_on_failure_returns_model_text():
    gateway = LLMGateway(FakeClient(scripted_responses=["I assumed X; next time I'll check Y."]))
    text = reflect_on_failure(gateway, "step failed: tests still red")
    assert text == "I assumed X; next time I'll check Y."


def test_reflect_on_failure_degrades_to_empty_on_error():
    class Broken:
        def complete(self, *a, **k):
            raise RuntimeError("boom")
    text = reflect_on_failure(LLMGateway(Broken()), "failure")
    assert text == ""


def test_run_reflection_respects_cap():
    gateway = LLMGateway(FakeClient(scripted_responses=["lesson 1", "lesson 2", "lesson 3", "lesson 4"]))
    reflection = RunReflection(cap=3)
    for _ in range(4):
        reflection.maybe_reflect(gateway, "some failure")
    assert reflection.used == 3
    assert len(reflection.lessons) == 3


def test_run_reflection_skips_empty_failure_context():
    gateway = LLMGateway(FakeClient(scripted_responses=["should not be consumed"]))
    reflection = RunReflection()
    reflection.maybe_reflect(gateway, "")
    assert reflection.used == 0


# -- raven/learn/extract_lessons.py -------------------------------------------

def test_extract_and_store_lesson_stores_valid_lesson(tmp_path):
    resp = (
        '```lesson\n{"trigger": "average() bug", "insight": "off-by-one in denominator", '
        '"action": "check +1", "scope": "repo", "confidence": 0.7}\n```'
    )
    gateway = LLMGateway(FakeClient(scripted_responses=[resp]))

    class FakeExecResult:
        aborted_reason = None
    class FakeVerdict:
        accepted = True
        reason = "ok"

    data = extract_and_store_lesson(gateway, tmp_path, "fix average()", FakeExecResult(), FakeVerdict(), {"evidence_score": 1.0})
    assert data["trigger"] == "average() bug"
    stored = lessons_mod.list_lessons(lessons_mod.repo_lessons_path(tmp_path))
    assert len(stored) == 1


def test_extract_and_store_lesson_skips_empty_trigger(tmp_path):
    gateway = LLMGateway(FakeClient(scripted_responses=['```lesson\n{"trigger": "", "insight": ""}\n```']))

    class FakeExecResult:
        aborted_reason = None
    class FakeVerdict:
        accepted = True
        reason = "ok"

    data = extract_and_store_lesson(gateway, tmp_path, "goal", FakeExecResult(), FakeVerdict(), None)
    assert data is None
    assert lessons_mod.list_lessons(lessons_mod.repo_lessons_path(tmp_path)) == []


def test_extract_and_store_lesson_never_raises_on_malformed_response(tmp_path):
    gateway = LLMGateway(FakeClient(scripted_responses=["not json at all"]))

    class FakeExecResult:
        aborted_reason = None
    class FakeVerdict:
        accepted = False
        reason = "failed"

    data = extract_and_store_lesson(gateway, tmp_path, "goal", FakeExecResult(), FakeVerdict(), None)
    assert data is None


# -- end-to-end: multi-issue session shows lesson reuse (plan §20 step 11) ---

def test_second_run_context_contains_lesson_extracted_from_first_run(tmp_path):
    """A run that fails (3 malformed actions -> abort) triggers an in-run
    reflection call and, at the end, a cross-task lesson extraction call.
    A SECOND, independent run against a related goal on the same repo must
    then see that lesson pinned in its context -- proving lessons persist
    and get retrieved across separate orchestrator runs, not just within
    one."""
    repo_root = tmp_path / "repo"
    shutil.copytree(TOY_REPO, repo_root)

    lesson_resp = (
        '```lesson\n{"trigger": "average() off-by-one bug", '
        '"insight": "average() denominator off-by-one needs careful arithmetic", '
        '"action": "read the function fully before editing", "scope": "repo", "confidence": 0.8}\n```'
    )
    run1_responses = [
        "I'm not sure what to do here.",
        "Still thinking about this.",
        "Let me try once more.",
        "I assumed the fix was obvious without reading the file first.",  # reflection call
        lesson_resp,  # end-of-run extraction call
    ]
    gateway1 = LLMGateway(FakeClient(scripted_responses=run1_responses))
    result1 = run_orchestrator(
        gateway1, repo_root, "average() off-by-one bug again", strategy="single_loop", runs_dir=tmp_path / "runs1",
    )
    assert not result1.accepted  # aborted after 3 malformed actions

    stored = lessons_mod.list_lessons(lessons_mod.repo_lessons_path(repo_root))
    assert len(stored) == 1
    assert "off-by-one" in stored[0]["insight"]

    run2_responses = [
        '```action\n{"tool": "done", "args": {"summary": "acknowledged"}}\n```',
        '```lesson\n{"trigger": ""}\n```',  # end-of-run extraction call, nothing new
    ]
    gateway2 = LLMGateway(FakeClient(scripted_responses=run2_responses))
    run_orchestrator(
        gateway2, repo_root, "average() off-by-one issue again", strategy="single_loop", runs_dir=tmp_path / "runs2",
    )

    sent_texts = " ".join(m.content for call in gateway2.client.calls for m in call)
    assert "# Lessons" in sent_texts
    assert "average() denominator off-by-one needs careful arithmetic" in sent_texts
