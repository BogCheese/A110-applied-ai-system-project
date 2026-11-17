"""Headless tests of the Streamlit page.

"Headless" means Streamlit runs app.py in memory and these tests click buttons and
read what the page shows, with no browser window. They catch bugs in the UI wiring
that the logic tests can't see. The AI is replaced by a scripted fake, so no network
or API key is needed.
"""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import pawpal_agent
from pawpal_system import Frequency, Pet, Priority, Task

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


class FakeLLM:
    def __init__(self, *tasks):
        self.reply = json.dumps({"tasks": list(tasks)})

    def complete(self, system, messages):
        return self.reply


def meal(time="18:00", title="Feeding"):
    return {"pet": "Nala", "title": title, "duration_minutes": 10, "priority": "high",
            "frequency": "daily", "preferred_time": time}


@pytest.fixture
def app(monkeypatch):
    """A running copy of the page with one cat named Nala, and a fake AI that adds a feeding."""
    monkeypatch.setattr(pawpal_agent, "make_client", lambda: FakeLLM(meal("18:00")))
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    at.session_state.owner.add_pet(Pet("Nala", "cat"))
    return at.run()


def click(at, label):
    [button for button in at.button if button.label == label][0].click()
    return at.run()


def pick(at, label, text):
    """Choose a dropdown option by the text the user sees (e.g. '8:30 AM')."""
    box = [b for b in at.selectbox if b.label == label][0]
    box.select_index(box.options.index(text))
    return at.run()


def page_text(at):
    """All the markdown the page shows, as one string."""
    return "\n".join(m.value for m in at.markdown)


# ---- first run and basics ----

def test_page_loads_without_errors():
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert not at.exception


def test_first_run_asks_for_a_pet_and_hides_the_rest():
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert any("Add your first pet" in i.value for i in at.info)
    labels = [b.label for b in at.button]
    assert "Plan with AI" not in labels and "Generate schedule" not in labels
    assert not at.metric


def test_species_choices_are_capitalized():
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    species = [s for s in at.selectbox if s.label == "Species"][0]
    assert list(species.options) == ["Dog", "Cat", "Other"]


def test_task_title_default_depends_on_species(app):
    owner = app.session_state.owner
    owner.add_pet(Pet("Rex", "dog"))
    owner.add_pet(Pet("Blob", "other"))
    titles = {}
    for index, pet in enumerate(owner.pets):
        [s for s in app.selectbox if s.label == "Add task for"][0].select(index)
        app.run()
        titles[pet.name] = [t for t in app.text_input if t.label == "New task title"][0].value
    assert titles == {"Nala": "Feed breakfast", "Rex": "Morning walk", "Blob": "Daily care"}


def test_every_input_has_a_unique_label(app):
    """Duplicate labels make controls ambiguous for screen reader users."""
    app.session_state.owner.pets[0].add_task(Task("Play", 10, Priority.LOW, Frequency.ONCE))
    app.run()
    widgets = [*app.selectbox, *app.text_input, *app.number_input, *app.text_area, *app.radio]
    labels = [w.label for w in widgets]
    assert len(labels) == len(set(labels)), sorted(l for l in labels if labels.count(l) > 1)


# ---- summary strip ----

def test_summary_strip_shows_counts(app):
    app.session_state.owner.pets[0].add_task(Task("Play", 30, Priority.LOW, Frequency.ONCE))
    app.run()
    values = {m.label: m.value for m in app.metric}
    assert values["Pets"] == "1" and values["Tasks to do"] == "1"
    assert values["Scheduled time"] == "30 / 120 min" and values["Conflicts"] == "0"


# ---- AI planner ----

def test_planner_adds_tasks_and_keeps_trace_after_other_clicks(app):
    app.text_area[0].set_value("plan Nala's day").run()
    click(app, "Plan with AI")
    assert not app.exception
    assert [t.title for t in app.session_state.owner.pets[0].tasks] == ["Feeding"]
    assert any(e.label == "What the agent did" for e in app.expander)

    click(app, "Generate schedule")  # a later click must not erase the trace
    assert any(e.label == "What the agent did" for e in app.expander)


def test_running_the_planner_twice_does_not_duplicate_tasks(app):
    app.text_area[0].set_value("plan").run()
    click(app, "Plan with AI")
    click(app, "Plan with AI")
    assert len(app.session_state.owner.pets[0].tasks) == 1
    assert any("already exist" in i.value for i in app.info)


def test_planner_error_is_shown_not_raised(app, monkeypatch):
    def broken():
        raise pawpal_agent.AgentError("No AI key found.")

    monkeypatch.setattr(pawpal_agent, "make_client", broken)
    app.text_area[0].set_value("plan").run()
    click(app, "Plan with AI")
    assert not app.exception
    assert any("No AI key found" in e.value for e in app.error)
    assert app.session_state.owner.pets[0].tasks == []


def test_example_prompt_button_fills_the_planner_box(app):
    click(app, "New pet routine")
    assert "Nala" in app.text_area[0].value


def test_planner_shows_the_user_what_the_ai_is_told(app):
    pick(app, "Day ends at (optional)", "9:00 PM")
    click(app, "Add busy time")  # 9:00 AM - 5:00 PM
    shown = page_text(app)
    assert "ends at 9:00 PM" in shown and "9:00 AM-5:00 PM" in shown and "Nala (cat)" in shown
    assert "Leave at least 5 minute(s) between tasks" in shown  # the default buffer


def test_owner_starts_with_a_five_minute_buffer(app):
    assert app.session_state.owner.preferences.buffer_minutes == 5


# ---- sidebar: day settings and busy times ----

def test_busy_time_can_be_added_and_removed(app):
    click(app, "Add busy time")  # form defaults to 9:00 AM - 5:00 PM
    assert app.session_state.owner.busy_blocks == [(9 * 60, 17 * 60)]
    click(app, "Delete busy time 9:00 AM - 5:00 PM")
    assert app.session_state.owner.busy_blocks == []


def test_day_start_and_end_update_the_owner(app):
    pick(app, "Day starts at", "9:30 AM")
    pick(app, "Day ends at (optional)", "8:00 PM")
    owner = app.session_state.owner
    assert (owner.day_start_minute, owner.day_end_minute) == (9 * 60 + 30, 20 * 60)
    pick(app, "Day ends at (optional)", "No end time")
    assert app.session_state.owner.day_end_minute is None


def test_end_before_start_is_rejected_with_a_warning(app):
    pick(app, "Day ends at (optional)", "7:00 AM")  # the day starts at 8:00 AM by default
    assert app.session_state.owner.day_end_minute is None
    assert any("must end after it starts" in w.value for w in app.warning)


def test_all_times_are_shown_in_standard_time(app):
    options = [box for box in app.selectbox if box.label == "Day starts at"][0].options
    assert "8:00 AM" in options and "1:05 PM" in options and "12:00 AM" in options
    assert not any(option.startswith(("13:", "20:")) for option in options)
    assert not app.time_input  # no 24-hour time boxes left on the page


# ---- tasks ----

def test_adding_a_task_uses_the_form_and_shows_a_confirmation(app):
    # Inside a form, a choice only counts when the form is submitted, so do both in one step.
    box = [b for b in app.selectbox if b.label == "New task preferred time"][0]
    box.select_index(box.options.index("6:15 PM"))
    click(app, "Add task")
    assert app.session_state.owner.pets[0].tasks[0].preferred_time == 18 * 60 + 15
    assert any("Added" in t.value for t in app.toast)


def test_task_table_shows_repeats_and_due_date(app):
    app.session_state.owner.pets[0].add_task(Task("Play", 10, Priority.LOW, Frequency.DAILY))
    app.run()
    table = app.table[-1].value
    assert "Repeats / due" in table.columns
    assert table["Repeats / due"][0] == "Daily, due today"


def test_task_can_be_edited_and_deleted(app):
    pet = app.session_state.owner.pets[0]
    pet.add_task(Task("Play", 10, Priority.LOW, Frequency.ONCE))
    app.run()

    [t for t in app.text_input if t.label == "Edit title"][0].set_value("Laser play")
    [n for n in app.number_input if n.label == "Edit minutes"][0].set_value(25)
    click(app, "Save changes")
    task = pet.tasks[0]
    assert (task.title, task.duration_minutes) == ("Laser play", 25)

    click(app, "Delete task")
    assert pet.tasks == []
    assert not app.exception


def test_mark_complete_creates_the_next_occurrence(app):
    pet = app.session_state.owner.pets[0]
    pet.add_task(Task("Feed", 10, Priority.HIGH, Frequency.DAILY))
    app.run()
    click(app, "Mark complete")
    assert [t.completed for t in pet.tasks] == [True, False]
    assert any("Done!" in t.value for t in app.toast)


# ---- schedule ----

def test_generated_schedule_persists_and_flags_when_out_of_date(app):
    pet = app.session_state.owner.pets[0]
    pet.add_task(Task("Play", 30, Priority.HIGH, Frequency.ONCE))
    app.run()
    click(app, "Generate schedule")
    assert any("Scheduled 1 task(s)" in s.value for s in app.success)

    app.run()  # an unrelated re-run must not erase it
    assert any("Scheduled 1 task(s)" in s.value for s in app.success)
    assert not any("changed since" in w.value for w in app.warning)

    pet.add_task(Task("Brush", 10, Priority.LOW, Frequency.ONCE))
    app.run()
    assert any("changed since this schedule was built" in w.value for w in app.warning)


def test_schedule_timeline_shows_busy_time_and_free_time(app):
    owner = app.session_state.owner
    owner.add_busy_block(9 * 60, 17 * 60)
    owner.pets[0].add_task(Task("Walk", 30, Priority.HIGH, Frequency.ONCE))
    app.run()
    click(app, "Generate schedule")
    text = page_text(app)
    assert "**8:00 AM – 8:30 AM** · Walk (Nala)" in text
    assert "Free time" in text and "Busy, no tasks" in text
