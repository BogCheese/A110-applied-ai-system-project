"""Shared building blocks for the PawPal+ pages: small helpers, callbacks, and the schedule view.

app.py (the entry point) and the files in app_pages/ import from here, so every page
formats times, shows the schedule, and edits tasks the same way.
"""

from datetime import time, timedelta

import streamlit as st

from calendar_view import render_calendar_html
from pawpal_agent import describe_context
from pawpal_system import Frequency, Owner, Priority, Scheduler, Task, format_time

# Gap left between consecutive tasks (travel, cleanup, a breather). Not user-editable.
BUFFER_MINUTES = 5

# Times are picked in steps of this many minutes (type a time, or open the list).
TIME_STEP_MINUTES = 15

# Starter task titles, so the form suggests something that makes sense for the pet.
DEFAULT_TASK_TITLES = {"dog": "Morning walk", "cat": "Feed breakfast"}

PRIORITY_ICONS = {Priority.HIGH: "🔴", Priority.MEDIUM: "🟡", Priority.LOW: "🟢"}
# Coloured badges in the task tables. The word is always shown too, so colour is never the only cue.
PRIORITY_BADGES = {Priority.HIGH: ":red-badge[High]", Priority.MEDIUM: ":orange-badge[Medium]", Priority.LOW: ":green-badge[Low]"}
SPECIES_BADGE_COLORS = {"dog": "blue", "cat": "orange"}  # anything else is gray

# Free gaps shorter than this are not worth listing in the timeline.

# Night mode. Streamlit can't switch its theme from Python, so this flips the whole page's
# colours with CSS instead: invert everything, then turn the hues back around so greens stay
# green. 0.93 (not 1.0) makes white come out as a soft dark grey instead of pure black.
NIGHT_MODE_CSS = """
<style>
html {
    filter: invert(0.93) hue-rotate(180deg);
    background: #ffffff;
}
</style>
"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


TIME_HELP = "Click a part (hour, minutes, AM/PM) and type. Press Enter to apply a change."
TIME_HELP_OPTIONAL = TIME_HELP + " To clear it, click the ✕ or backspace every part and press Enter."


def time_picker(label: str, value: int | None, key: str, optional: bool = False) -> int | None:
    """A 12-hour time box (e.g. '8:30 AM') that you can type in or pick from. Returns minutes since midnight.

    The list steps by 15 minutes, which keeps it short. Pass optional=True to allow an empty
    box, which returns None ("no time"); the label should then say "(optional)".
    """
    # The starting time goes into session state, not into `value=`: when a box is emptied
    # Streamlit hands back its `value=` default, so a default of 9:00 AM could never be cleared.
    if key not in st.session_state:
        st.session_state[key] = None if value is None else time(value // 60 % 24, value % 60)
    picked = st.time_input(
        label,
        value=None,
        key=key,
        step=timedelta(minutes=TIME_STEP_MINUTES),
        format="12h",
        required=not optional,
        help=TIME_HELP_OPTIONAL if optional else TIME_HELP,
    )
    return None if picked is None else picked.hour * 60 + picked.minute


def md_escape(text: str) -> str:
    """Escape characters Markdown would otherwise act on (bold, links, and $ for maths) in user text."""
    for char in "\\`*_[]$<>|":
        text = text.replace(char, "\\" + char)
    return text


def flash(message: str) -> None:
    """Queue a small pop-up message to show after the page re-runs."""
    st.session_state.setdefault("flash", []).append(message)


def show_flashes() -> None:
    """Show (and clear) the queued pop-up messages."""
    for message in st.session_state.pop("flash", []):
        st.toast(message)


def apply_night_mode() -> None:
    """Switch the page to night colours when the toggle is on.

    Skipped when Streamlit's own dark theme is already active (chosen in its Settings menu or
    by the computer's dark mode), because inverting an already dark page would make it light again.
    """
    if not st.session_state.get("night_mode"):
        return
    try:
        already_dark = st.context.theme.type == "dark"
    except Exception:  # the theme isn't known on the very first run
        already_dark = False
    if not already_dark:
        st.html(NIGHT_MODE_CSS)


def context_markdown(owner: Owner) -> str:
    """The AI's context (see describe_context) laid out as a readable, wrapping list."""
    return describe_context(owner).replace("\n    - ", "\n  - ")


# ---------------------------------------------------------------------------
# Button callbacks (they run before the page re-runs)
# ---------------------------------------------------------------------------


def ask_reset() -> None:
    st.session_state.confirm_reset = True


def cancel_reset() -> None:
    st.session_state.confirm_reset = False


def reset_app() -> None:
    """Forget everything (pets, tasks, AI result, and every widget's value)."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def toggle_done(task_id: str) -> None:
    """Checkbox callback: tick marks the task done, untick takes it back (and any copy it made).

    This runs before the page re-runs, so the next draw already shows the new state.
    """
    owner = st.session_state.owner
    for pet in owner.pets:
        for task in pet.tasks:
            if task.id != task_id:
                continue
            ticked = st.session_state.get(f"done_{task_id}", task.completed)
            if ticked and not task.completed:
                try:
                    # Adds the next occurrence if the task repeats, and remembers the task's time slot
                    # so ticking it never moves it.
                    next_task = Scheduler(owner).complete_task(pet, task)
                except ValueError as error:
                    flash(str(error))
                    return
                if next_task:
                    flash(
                        f"Done! '{task.title}' repeats {task.frequency.value}, "
                        f"next one is due {next_task.due_date:%b %d}."
                    )
                else:
                    flash(f"Done! '{task.title}' is complete.")
            elif not ticked and task.completed:
                pet.reopen_task(task)  # also takes back the next copy that completing it created
                flash(f"'{task.title}' is back on your to-do list.")
            return


def open_editor(task_id: str) -> None:
    """Edit button callback: remember which task's edit window to show."""
    st.session_state.editing_task = task_id


def close_editor() -> None:
    """Close the edit window and forget what was typed in it, so it opens fresh next time."""
    task_id = st.session_state.pop("editing_task", None)
    for field in ("title", "minutes", "priority", "often", "time"):
        st.session_state.pop(f"edit_{field}_{task_id}", None)


# ---------------------------------------------------------------------------
# Editing a task in a dialog
# ---------------------------------------------------------------------------


@st.dialog("Edit task", on_dismiss=close_editor)
def edit_task_dialog(owner: Owner, task: Task) -> None:
    """The edit window: change a task's details, or delete it."""
    pet = next(p for p in owner.pets if any(t.id == task.id for t in p.tasks))
    st.caption(f"For {pet.name}")
    with st.form(f"edit_form_{task.id}", border=False):
        title_field, minutes_field = st.columns([3, 1])
        new_title = title_field.text_input("Title", value=task.title, key=f"edit_title_{task.id}")
        new_duration = minutes_field.number_input(
            "Minutes", min_value=1, max_value=240, value=min(task.duration_minutes, 240), key=f"edit_minutes_{task.id}"
        )
        new_priority = st.segmented_control(
            "Priority",
            list(Priority),
            default=task.priority,
            required=True,
            format_func=lambda p: p.name.title(),
            key=f"edit_priority_{task.id}",
        )
        new_frequency = st.segmented_control(
            "Repeats",
            list(Frequency),
            default=task.frequency,
            required=True,
            format_func=lambda f: f.value.title(),
            key=f"edit_often_{task.id}",
        )
        new_time = time_picker(
            "Preferred time (optional)", task.preferred_time, key=f"edit_time_{task.id}", optional=True
        )
        with st.container(horizontal=True):
            save_clicked = st.form_submit_button(
                "Save changes", icon=":material/save:", key=f"edit_save_{task.id}"
            )
            delete_clicked = st.form_submit_button("Delete task", icon=":material/delete:", key=f"edit_delete_{task.id}")

    if save_clicked:
        pet.edit_task(
            task,
            title=new_title.strip() or task.title,
            duration_minutes=int(new_duration),
            priority=new_priority,
            frequency=new_frequency,
            preferred_time=new_time,
        )
        flash(f"Saved changes to '{task.title}'.")
        close_editor()
        st.rerun()
    if delete_clicked:
        pet.remove_task(task)
        flash(f"Deleted '{task.title}'.")
        close_editor()
        st.rerun()


def show_editor_if_open(owner: Owner) -> None:
    """Open the edit window for the task chosen with an Edit button, if there is one."""
    task_id = st.session_state.get("editing_task")
    if task_id is None:
        return
    task = next((t for t in owner.get_all_tasks() if t.id == task_id), None)
    if task is None:  # it was deleted, so there is nothing to edit
        close_editor()
        return
    edit_task_dialog(owner, task)


def edit_button(task: Task) -> None:
    """The Edit button shown next to a task."""
    st.button(
        "Edit",
        icon=":material/edit:",
        key=f"edit_open_{task.id}",
        on_click=open_editor,
        args=(task.id,),
        width="stretch",
        help=f"Edit {task.title} ({task.pet_name})",
    )


# ---------------------------------------------------------------------------
# The schedule: calendar, list, and tasks that are not on today's list
# ---------------------------------------------------------------------------


def build_schedule_view(owner: Owner, plan) -> dict:
    """Turn a DailyPlan into plain data for drawing the calendar and the list."""
    entries = []  # (start, end, text, task or None) for the list view
    blocks = []  # structured data for the calendar view
    for item in plan.scheduled_items:
        task = item.task
        blocks.append(
            {
                "kind": "task",
                "start": item.start_minute,
                "end": item.end_minute,
                "title": task.title,
                "pet": task.pet_name,
                "priority": task.priority.name.lower(),
                "reason": item.reason.split(", ", 1)[-1],
                "done": item.done,
            }
        )
        label = f"{md_escape(task.title)} ({md_escape(task.pet_name)})"
        entries.append(
            (
                item.start_minute,
                item.end_minute,
                # A finished task keeps its place in the list, crossed out (and ticked by its checkbox).
                (f"~~{label}~~" if item.done else label)
                + f" · {PRIORITY_ICONS[task.priority]} {task.priority.name.title()} priority"
                # Say so when the task couldn't go at the time it asked for (taken, busy, or no buffer room).
                + (
                    f" · ⏱ wanted {format_time(task.preferred_time)}"
                    if task.preferred_time is not None and task.preferred_time != item.start_minute
                    else ""
                ),
                task,
            )
        )
    for start, end in owner.busy_blocks:
        entries.append((start, end, "🚫 Busy, no tasks", None))
        blocks.append({"kind": "busy", "start": start, "end": end})
    entries.sort(key=lambda entry: entry[:3])

    timeline = entries

    # Tasks that are neither on today's plan nor left out of it (due later, or finished on another day).
    planned_ids = {item.task.id for item in plan.scheduled_items} | {s.task.id for s in plan.skipped_items}
    later = [task for task in owner.get_all_tasks() if task.id not in planned_ids]

    return {
        "scheduled_count": len(plan.scheduled_items),
        "done_count": len([item for item in plan.scheduled_items if item.done]),
        "total_minutes": plan.total_minutes,
        "available_minutes": owner.available_minutes,
        "timeline": timeline,
        "blocks": blocks,
        "pet_names": [pet.name for pet in owner.pets],
        "day_start": owner.day_start_minute,
        "day_end": owner.day_end_minute,
        "skipped": [(s.task, s.reason) for s in plan.skipped_items],
        "later": later,
    }


def render_schedule(view: dict) -> None:
    """Draw a schedule: a calendar of time blocks, with the same thing as a list beside it.

    In the list each task has a checkbox (tick = done, untick = not done) and an Edit button.
    """
    if view["scheduled_count"]:
        st.success(
            f"Scheduled {view['scheduled_count']} task(s)"
            + (f" ({view['done_count']} done)" if view["done_count"] else "")
            + f" using {view['total_minutes']} of {view['available_minutes']} minutes of task time "
            f"(gaps between tasks aren't counted)."
        )
        calendar_col, list_col = st.columns([3, 2], gap="large")
        with calendar_col:
            st.html(render_calendar_html(view["blocks"], view["pet_names"], view["day_start"], view["day_end"]))
        with list_col:
            with st.container(border=True):
                st.markdown("**List view**")
                for start, end, text, task in view["timeline"]:
                    check_col, text_col, edit_col = st.columns([1, 6, 2], vertical_alignment="center")
                    text_col.markdown(f"**{format_time(start)} – {format_time(end)}** · {text}")
                    if task is None:
                        continue  # free and busy time have nothing to tick or edit
                    # Keep the box in step with the task, whichever way it was last changed.
                    st.session_state[f"done_{task.id}"] = task.completed
                    check_col.checkbox(
                        f"Mark {task.title} ({task.pet_name}) done",
                        key=f"done_{task.id}",
                        on_change=toggle_done,
                        args=(task.id,),
                        label_visibility="collapsed",
                    )
                    with edit_col:
                        edit_button(task)
    else:
        st.warning("Nothing could be scheduled. Add tasks or increase your available time.")

    if view["skipped"]:
        with st.container(border=True):
            st.warning("Some tasks were left out. Edit one to shorten it or move it to another time.")
            for task, reason in view["skipped"]:
                text_col, edit_col = st.columns([7, 2], vertical_alignment="center")
                text_col.markdown(f"**{md_escape(task.title)}** ({md_escape(task.pet_name)}): {reason}")
                with edit_col:
                    edit_button(task)

    if view["later"]:
        with st.expander(f"Not on today's schedule ({len(view['later'])})"):
            for task in view["later"]:
                text_col, edit_col = st.columns([7, 2], vertical_alignment="center")
                when = "done" if task.completed else f"due {task.due_date:%b %d}"
                text_col.markdown(f"**{md_escape(task.title)}** ({md_escape(task.pet_name)}) · {when}")
                with edit_col:
                    edit_button(task)
