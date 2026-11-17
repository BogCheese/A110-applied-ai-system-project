"""Tests for the planning agent, using a scripted fake model (no network)."""

import json

import pytest

from pawpal_agent import AgentError, PlannerAgent, extract_json, parse_time, validate_tasks
from pawpal_system import Frequency, Owner, Pet, Priority, Task


class FakeLLM:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def complete(self, system, messages):
        self.calls.append(list(messages))
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


def reply(*tasks):
    keys = ("pet", "title", "duration_minutes", "priority", "frequency", "preferred_time")
    return json.dumps({"tasks": [dict(zip(keys, t)) for t in tasks]})


@pytest.fixture
def owner():
    o = Owner("Jordan", 60)
    o.add_pet(Pet("Mia", "cat"))
    return o


def test_good_plan_commits_in_one_attempt(owner):
    llm = FakeLLM(reply(("Mia", "Feeding", 10, "high", "daily", "08:00")))
    result = PlannerAgent(llm).run(owner, "feed my cat")
    assert result.success and result.attempts == 1
    task = owner.pets[0].tasks[0]
    assert (task.title, task.priority, task.frequency, task.preferred_time) == ("Feeding", Priority.HIGH, Frequency.DAILY, 480)


def test_conflict_is_sent_back_and_revised(owner):
    bad = reply(("Mia", "Feeding", 30, "high", "daily", "08:00"), ("Mia", "Play", 20, "low", "daily", "08:10"))
    good = reply(("Mia", "Feeding", 30, "high", "daily", "08:00"), ("Mia", "Play", 20, "low", "daily", "09:00"))
    llm = FakeLLM(bad, good)
    result = PlannerAgent(llm).run(owner, "plan")
    assert result.success and result.attempts == 2
    assert "Conflict" in llm.calls[1][-1]["content"]  # the scheduler's warning reached the model
    assert len(owner.pets[0].tasks) == 2  # only the revised plan was committed


def test_task_that_does_not_fit_triggers_revision(owner):
    llm = FakeLLM(
        reply(("Mia", "Marathon", 200, "high", "once", None)),
        reply(("Mia", "Walk", 20, "high", "once", None)),
    )
    result = PlannerAgent(llm).run(owner, "plan")
    assert result.success and [t.title for t in owner.pets[0].tasks] == ["Walk"]


def test_unresolved_problems_are_reported_not_hidden(owner):
    same_conflict = reply(("Mia", "A", 30, "high", "once", "08:00"), ("Mia", "B", 30, "high", "once", "08:10"))
    result = PlannerAgent(FakeLLM(same_conflict), max_revisions=1).run(owner, "plan")
    assert result.committed and not result.success and result.issues
    assert result.attempts == 2


def test_garbage_then_valid_reply_recovers(owner):
    llm = FakeLLM("no json here", reply(("Mia", "Feeding", 10, "high", "daily", None)))
    assert PlannerAgent(llm).run(owner, "plan").success


def test_never_valid_leaves_owner_unchanged(owner):
    with pytest.raises(AgentError):
        PlannerAgent(FakeLLM("nope"), max_revisions=1).run(owner, "plan")
    assert owner.get_all_tasks() == []


def test_llm_crash_becomes_agent_error(owner):
    class Boom:
        def complete(self, system, messages):
            raise RuntimeError("network down")

    with pytest.raises(AgentError):
        PlannerAgent(Boom()).run(owner, "plan")
    assert owner.get_all_tasks() == []


def test_existing_conflicts_are_not_blamed_on_agent(owner):
    from pawpal_system import Task

    owner.available_minutes = 120  # room for the two old tasks plus the new one
    owner.pets[0].add_task(Task("Old A", 30, Priority.LOW, preferred_time=600))
    owner.pets[0].add_task(Task("Old B", 30, Priority.LOW, preferred_time=610))
    result = PlannerAgent(FakeLLM(reply(("Mia", "Feeding", 10, "high", "daily", "08:00")))).run(owner, "plan")
    assert result.success


def test_empty_goal_and_no_pets_rejected(owner):
    with pytest.raises(AgentError):
        PlannerAgent(FakeLLM("{}")).run(owner, "   ")
    with pytest.raises(AgentError):
        PlannerAgent(FakeLLM("{}")).run(Owner("X", 30), "plan")


def test_validate_rejects_bad_items():
    raw = [
        {"pet": "Ghost", "title": "x", "duration_minutes": 5, "priority": "high"},
        {"pet": "Mia", "title": "x", "duration_minutes": 0, "priority": "high"},
        {"pet": "Mia", "title": "x", "duration_minutes": True, "priority": "high"},
        {"pet": "Mia", "title": "x", "duration_minutes": 5, "priority": "urgent"},
        {"pet": "Mia", "title": "x", "duration_minutes": 5, "priority": "low", "preferred_time": "25:99"},
        "not a dict",
        {"pet": "mia", "title": "ok", "duration_minutes": 5, "priority": "LOW"},
    ]
    specs, errors = validate_tasks(raw, ["Mia"])
    assert len(specs) == 1 and specs[0].pet == "Mia" and len(errors) == 6


def test_parse_time_and_extract_json():
    assert parse_time("08:30") == 510 and parse_time(None) is None
    with pytest.raises(ValueError):
        parse_time("8am")
    assert extract_json('```json\n{"tasks": []}\n```') == {"tasks": []}
    with pytest.raises(ValueError):
        extract_json('{"tasks": "no"}')


def test_make_client_picks_provider_from_env(monkeypatch):
    from pawpal_agent import GeminiClient, make_client

    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "PAWPAL_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(AgentError):
        make_client()
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert isinstance(make_client(), GeminiClient)
    monkeypatch.setenv("PAWPAL_PROVIDER", "bogus")
    with pytest.raises(AgentError):
        make_client()


def test_gemini_client_parses_response_and_errors(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    from pawpal_agent import GeminiClient

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    payload = {"candidates": [{"content": {"parts": [{"text": '{"tasks": []}'}]}}]}
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: io.StringIO(json.dumps(payload)))
    assert GeminiClient().complete("sys", [{"role": "user", "content": "hi"}]) == '{"tasks": []}'

    def rate_limited(req, timeout):
        raise urllib.error.HTTPError("u", 429, "Too Many", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", rate_limited)
    client = GeminiClient()
    client._sleep = lambda seconds: None  # don't really wait between retries
    with pytest.raises(AgentError, match="429"):
        client.complete("sys", [{"role": "user", "content": "hi"}])


def test_load_env_reads_file_without_overriding(tmp_path, monkeypatch):
    from pawpal_agent import load_env

    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nPAWPAL_A="from-file"\nPAWPAL_B=file-b\n\n')
    monkeypatch.delenv("PAWPAL_A", raising=False)
    monkeypatch.setenv("PAWPAL_B", "real")
    load_env(str(env_file))
    import os

    assert os.environ["PAWPAL_A"] == "from-file" and os.environ["PAWPAL_B"] == "real"
    monkeypatch.delenv("PAWPAL_A")
    load_env(str(tmp_path / "missing.env"))  # no file: no error


def test_gemini_retries_then_succeeds(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    from pawpal_agent import GeminiClient

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    outcomes = [urllib.error.HTTPError("u", 503, "Busy", {}, None), io.StringIO(json.dumps({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}))]
    waits = []

    def flaky(req, timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    client = GeminiClient()
    client._sleep = waits.append
    assert client.complete("s", [{"role": "user", "content": "hi"}]) == "ok"
    assert waits == [3]  # waited once, then the retry worked


def test_second_feeding_at_a_different_time_is_not_a_duplicate(owner):
    owner.pets[0].add_task(Task("Feeding", 10, Priority.HIGH, Frequency.DAILY, preferred_time=8 * 60))
    llm = FakeLLM(reply(("Mia", "feeding", 10, "high", "daily", "18:00")))
    result = PlannerAgent(llm).run(owner, "second meal")
    assert result.success and result.duplicates == []
    assert len(owner.pets[0].tasks) == 2


def test_same_title_and_time_is_skipped_as_duplicate(owner):
    owner.pets[0].add_task(Task("Feeding", 10, Priority.HIGH, Frequency.DAILY, preferred_time=8 * 60))
    llm = FakeLLM(reply(("Mia", "FEEDING", 10, "high", "daily", "08:00"), ("Mia", "Play", 15, "low", "daily", "09:00")))
    result = PlannerAgent(llm).run(owner, "plan")
    assert result.success and len(result.duplicates) == 1
    assert [t.title for t in owner.pets[0].tasks] == ["Feeding", "Play"]
    assert any("Skipped duplicate" in step.detail for step in result.steps)


def test_all_duplicates_adds_nothing_and_still_succeeds(owner):
    owner.pets[0].add_task(Task("Feeding", 10, Priority.HIGH, Frequency.DAILY, preferred_time=8 * 60))
    result = PlannerAgent(FakeLLM(reply(("Mia", "Feeding", 10, "high", "daily", "08:00")))).run(owner, "plan")
    assert result.success and result.tasks == [] and len(owner.pets[0].tasks) == 1


def test_duplicates_inside_one_reply_are_collapsed(owner):
    llm = FakeLLM(reply(("Mia", "Play", 10, "low", "daily", "09:00"), ("Mia", "play", 10, "low", "daily", "09:00")))
    result = PlannerAgent(llm).run(owner, "plan")
    assert len(owner.pets[0].tasks) == 1 and len(result.duplicates) == 1


def test_busy_time_and_day_window_are_checked_and_sent_back(owner):
    owner.add_busy_block(9 * 60, 17 * 60)  # work 9-5
    owner.day_end_minute = 21 * 60
    bad = reply(("Mia", "Play", 20, "low", "daily", "10:00"), ("Mia", "Late meal", 10, "high", "daily", "21:00"))
    good = reply(("Mia", "Play", 20, "low", "daily", "17:30"), ("Mia", "Late meal", 10, "high", "daily", "20:00"))
    llm = FakeLLM(bad, good)
    result = PlannerAgent(llm).run(owner, "plan around my job")
    assert result.success and result.attempts == 2
    feedback = llm.calls[1][-1]["content"]
    assert "busy time" in feedback and "runs past the day end" in feedback


def test_prompt_lists_existing_tasks_and_the_owners_day(owner):
    from pawpal_agent import PlannerAgent as Agent
    from pawpal_system import Task as T

    owner.pets[0].add_task(T("Feeding", 10, Priority.HIGH, Frequency.DAILY, preferred_time=8 * 60))
    owner.add_busy_block(9 * 60, 17 * 60)
    owner.day_end_minute = 22 * 60
    text = Agent._describe_request(owner, "goal")
    assert "Feeding: 10 min, high, daily at 8:00 AM" in text
    assert "ends at 10:00 PM" in text and "9:00 AM-5:00 PM" in text


def test_tasks_closer_than_the_buffer_are_sent_back(owner):
    owner.preferences.buffer_minutes = 5
    tight = reply(("Mia", "Feeding", 15, "high", "daily", "08:00"), ("Mia", "Play", 15, "low", "daily", "08:17"))  # 2 min gap
    fixed = reply(("Mia", "Feeding", 15, "high", "daily", "08:00"), ("Mia", "Play", 15, "low", "daily", "08:20"))  # 5 min gap
    llm = FakeLLM(tight, fixed)
    result = PlannerAgent(llm).run(owner, "plan")
    assert result.success and result.attempts == 2
    assert "at least 5 minutes between tasks" in llm.calls[1][-1]["content"]


def test_buffer_is_not_enforced_when_zero(owner):
    back_to_back = reply(("Mia", "Feeding", 15, "high", "daily", "08:00"), ("Mia", "Play", 15, "low", "daily", "08:15"))
    assert PlannerAgent(FakeLLM(back_to_back)).run(owner, "plan").attempts == 1


def test_context_mentions_the_buffer_only_when_set(owner):
    from pawpal_agent import describe_context

    assert "between tasks" not in describe_context(owner)
    owner.preferences.buffer_minutes = 5
    assert "Leave at least 5 minute(s) between tasks" in describe_context(owner)


def test_total_over_budget_is_reported_once_with_the_numbers(owner):
    over = reply(("Mia", "Walk", 40, "high", "once", None), ("Mia", "Play", 40, "low", "once", None))  # 80 > 60
    llm = FakeLLM(over, reply(("Mia", "Walk", 40, "high", "once", None), ("Mia", "Play", 15, "low", "once", None)))
    result = PlannerAgent(llm).run(owner, "plan")
    feedback = llm.calls[1][-1]["content"]
    assert "All tasks together take 80 min but the owner only has 60 min (over by 20)" in feedback
    assert feedback.count("does not fit") == 0  # not repeated per task
    assert result.success


def test_existing_task_pushed_out_by_new_tasks_is_reported(owner):
    owner.available_minutes = 60
    owner.pets[0].add_task(Task("Old grooming", 30, Priority.LOW))
    llm = FakeLLM(reply(("Mia", "Big walk", 45, "high", "once", None)), reply(("Mia", "Walk", 20, "high", "once", None)))
    result = PlannerAgent(llm).run(owner, "plan")
    assert result.attempts == 2 and result.success
    assert "Old grooming" in llm.calls[1][-1]["content"]


def test_context_tells_the_model_how_many_minutes_are_left(owner):
    from pawpal_agent import describe_context

    owner.pets[0].add_task(Task("Feeding", 25, Priority.HIGH))
    text = describe_context(owner)
    assert "already use 25 of those minutes, so 35 minutes remain" in text
