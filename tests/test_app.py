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


# ---- AI planner ----

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

def test_no_sidebar_and_owner_name_is_editable(app):
    assert len(app.sidebar) == 0  # everything lives on the main page
    name_box = [t for t in app.text_input if t.label == "Pet Owner"][0]
    assert name_box.value == "" and name_box.placeholder == "Name"  # greyed hint, not a typed name
    name_box.set_value("Brandon").run()
    assert app.session_state.owner.name == "Brandon"


def calendar_html(at):
    return "\n".join(h.value for h in at.get("html"))


# ---- AI planner (trace stays visible) ----

def test_planner_adds_tasks_and_keeps_trace_after_other_clicks(app):
    app.text_area[0].set_value("plan Nala's day").run()
    click(app, "Plan with AI")
    assert not app.exception
    assert [t.title for t in app.session_state.owner.pets[0].tasks] == ["Feeding"]
    assert any(e.label == "What the agent did" for e in app.expander)

    pick(app, "Day ends at (optional)", "9:00 PM")  # a later interaction must not erase the trace
    assert any(e.label == "What the agent did" for e in app.expander)


# ---- the schedule builds itself ----

def test_there_is_no_generate_button_and_the_schedule_appears_by_itself(app):
    assert "Generate schedule" not in [b.label for b in app.button]
    assert "Day schedule calendar" not in calendar_html(app)  # no tasks yet
    app.session_state.owner.pets[0].add_task(Task("Play", 30, Priority.HIGH, Frequency.ONCE))
    app.run()
    assert "Day schedule calendar" in calendar_html(app) and "Play" in calendar_html(app)
    assert any("Scheduled 1 task(s)" in s.value for s in app.success)
    assert any("gaps between tasks aren't counted" in s.value for s in app.success)


def test_schedule_follows_changes_without_any_click(app):
    pet = app.session_state.owner.pets[0]
    pet.add_task(Task("Play", 30, Priority.HIGH, Frequency.ONCE))
    app.run()
    pet.add_task(Task("Brush", 10, Priority.LOW, Frequency.ONCE))
    app.run()
    assert "Brush" in calendar_html(app) and any("Scheduled 2 task(s)" in s.value for s in app.success)


def test_schedule_timeline_shows_busy_time_and_free_time(app):
    owner = app.session_state.owner
    owner.add_busy_block(9 * 60, 17 * 60)
    owner.pets[0].add_task(Task("Walk", 30, Priority.HIGH, Frequency.ONCE))
    app.run()
    text = page_text(app)
    assert "**8:00 AM – 8:30 AM** · Walk (Nala)" in text
    assert "Free time" in text and "Busy, no tasks" in text
    assert not app.metric  # no summary strip on the page
    assert "List view" in text and "placed at its preferred time" not in text


def test_before_any_pet_the_right_side_invites_you_to_start():
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert "Your schedule will appear here" in page_text(at)
    assert "Day schedule calendar" not in calendar_html(at)


# ---- tasks grouped by pet ----

def test_tasks_are_grouped_by_pet_with_time_first_and_priority_badges(app):
    owner = app.session_state.owner
    owner.add_pet(Pet("Mocha", "dog"))
    owner.pets[0].add_task(Task("Late brush", 10, Priority.LOW, Frequency.WEEKLY, preferred_time=18 * 60))
    owner.pets[0].add_task(Task("Early feed", 10, Priority.HIGH, Frequency.DAILY, preferred_time=8 * 60))
    owner.pets[1].add_task(Task("Walk", 20, Priority.MEDIUM, Frequency.DAILY))
    app.run()
    tables = [t.value for t in app.table]
    assert len(tables) == 2  # one per pet
    nala, mocha = tables
    assert list(nala.columns)[0] == "Time"
    assert list(nala["Task"]) == ["Early feed", "Late brush"]  # earliest first
    assert list(nala["Priority"]) == [":red-badge[High]", ":green-badge[Low]"]
    assert list(mocha["Time"]) == ["Any time"] and mocha["Priority"][0] == ":orange-badge[Medium]"


def test_status_filter_narrows_the_tables(app):
    pet = app.session_state.owner.pets[0]
    pet.add_task(Task("Open", 10, Priority.LOW, Frequency.ONCE))
    finished = Task("Finished", 10, Priority.LOW, Frequency.ONCE)
    pet.add_task(finished)
    finished.completed = True
    app.run()
    assert len(app.table[0].value) == 2
    app.segmented_control[0].set_value("Done").run()
    assert list(app.table[0].value["Task"]) == ["Finished"]
    app.segmented_control[0].set_value("To do").run()
    assert list(app.table[0].value["Task"]) == ["Open"]


def test_task_titles_with_markdown_characters_are_shown_literally(app):
    app.session_state.owner.pets[0].add_task(Task("50% *off* $5", 10, Priority.LOW, Frequency.ONCE))
    app.run()
    assert app.table[0].value["Task"][0] == "50% \\*off\\* \\$5"


# ---- pet cards ----

def test_pet_can_be_deleted_after_confirming(app):
    app.session_state.owner.pets[0].add_task(Task("Play", 10, Priority.LOW, Frequency.ONCE))
    app.run()
    click(app, "Delete Nala")
    assert app.session_state.owner.pets  # nothing happens until confirmed
    assert "Delete **Nala** and 1 task(s)?" in page_text(app)
    click(app, "Yes, delete")
    assert app.session_state.owner.pets == []
    assert any("Deleted Nala" in t.value for t in app.toast)
    assert "Your schedule will appear here" in page_text(app)


def test_cancelling_a_pet_deletion_keeps_the_pet(app):
    click(app, "Delete Nala")
    click(app, "Cancel")
    assert [p.name for p in app.session_state.owner.pets] == ["Nala"]
    assert "Delete Nala" in [b.label for b in app.button]  # back to the normal card


# ---- start over ----

def test_start_over_clears_everything_after_confirming(app):
    app.session_state.owner.pets[0].add_task(Task("Play", 10, Priority.LOW, Frequency.ONCE))
    app.text_area[0].set_value("some goal").run()
    pick(app, "Day starts at", "9:30 AM")
    click(app, "Start over")
    assert app.session_state.owner.pets  # asks first
    assert "Start over?" in page_text(app)
    click(app, "Yes, start over")
    owner = app.session_state.owner
    assert owner.pets == [] and owner.day_start_minute == 8 * 60
    assert not app.text_area  # the planner box is hidden until there is a pet again
    assert "planner_goal" not in app.session_state  # and its text is gone


def test_cancelling_start_over_keeps_your_data(app):
    click(app, "Start over")
    click(app, "Cancel")
    assert [p.name for p in app.session_state.owner.pets] == ["Nala"]
    assert "Start over?" not in page_text(app)


# ---- theme ----

def test_theme_file_is_valid_and_primary_colour_is_readable_with_white_text():
    import tomllib

    config = tomllib.loads((Path(APP_PATH).parent / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    theme = config["theme"]

    def luminance(hex_colour):
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    contrast = 1.05 / (luminance(theme["primaryColor"]) + 0.05)  # white text on the primary colour
    assert contrast >= 4.5, f"primary colour contrast is only {contrast:.2f}:1"
    assert "light" in theme and "dark" in theme  # both appearances are styled


# ---- night mode and background ----

def test_night_mode_toggle_adds_and_removes_the_dark_css(app):
    assert "invert(" not in calendar_html(app)  # off by default
    [t for t in app.toggle if "Night mode" in t.label][0].set_value(True).run()
    assert not app.exception
    assert "invert(0.93) hue-rotate(180deg)" in calendar_html(app)
    [t for t in app.toggle if "Night mode" in t.label][0].set_value(False).run()
    assert "invert(" not in calendar_html(app)


def test_night_mode_does_not_invert_when_streamlits_dark_theme_is_active(app, monkeypatch):
    import streamlit as st

    class DarkTheme:
        type = "dark"

    monkeypatch.setattr(type(st.context), "theme", property(lambda self: DarkTheme()))
    [t for t in app.toggle if "Night mode" in t.label][0].set_value(True).run()
    assert "invert(" not in calendar_html(app)  # inverting an already dark page would make it light


def test_light_theme_background_is_plain_white():
    import tomllib

    config = tomllib.loads((Path(APP_PATH).parent / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["theme"]["light"]["backgroundColor"].lower() == "#ffffff"
