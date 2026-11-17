import streamlit as st

from datetime import date

from calendar_view import render_calendar_html
from pawpal_agent import AgentError, PlannerAgent, describe_context, make_client, setup_logging
from pawpal_system import Frequency, Owner, Pet, Preferences, Priority, Scheduler, Task, format_time

setup_logging()

# Gap left between consecutive tasks (travel, cleanup, a breather). Not user-editable.
BUFFER_MINUTES = 5

# Starter task titles, so the form suggests something that makes sense for the pet.
DEFAULT_TASK_TITLES = {"dog": "Morning walk", "cat": "Feed breakfast"}

PRIORITY_ICONS = {Priority.HIGH: "🔴", Priority.MEDIUM: "🟡", Priority.LOW: "🟢"}
# Coloured badges in the task tables. The word is always shown too, so colour is never the only cue.
PRIORITY_BADGES = {Priority.HIGH: ":red-badge[High]", Priority.MEDIUM: ":orange-badge[Medium]", Priority.LOW: ":green-badge[Low]"}
SPECIES_BADGE_COLORS = {"dog": "blue", "cat": "orange"}  # anything else is gray

# Free gaps shorter than this are not worth listing in the timeline.
MIN_FREE_GAP_MINUTES = 10

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


def time_picker(label: str, value: int | None, key: str, none_label: str | None = None) -> int | None:
    """A dropdown of times in standard (12-hour) format, e.g. '8:00 AM'. Returns minutes since midnight.

    Streamlit's own time box follows the browser's clock settings and often shows 24-hour
    time, so a dropdown is used to keep every time in the same readable format. Times
    step by 5 minutes; a value that isn't on a step (e.g. from the AI) is added so it
    still shows correctly. Pass none_label (e.g. "No end time") to allow "no value".
    """
    options = list(range(0, 24 * 60, 5))
    if value is not None and value not in options:
        options = sorted(options + [value])
    if none_label is not None:
        options = [None] + options
    return st.selectbox(
        label,
        options,
        index=options.index(value),
        format_func=lambda minute: none_label if minute is None else format_time(minute),
        key=key,
    )


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


def build_schedule_view(owner: Owner, plan) -> dict:
    """Turn a DailyPlan into plain data for drawing the calendar and the list."""
    entries = []  # (start, end, text) for the list view
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
            }
        )
        entries.append(
            (
                item.start_minute,
                item.end_minute,
                f"{md_escape(task.title)} ({md_escape(task.pet_name)}) · "
                f"{PRIORITY_ICONS[task.priority]} {task.priority.name.title()} priority",
            )
        )
    for start, end in owner.busy_blocks:
        entries.append((start, end, "🚫 Busy, no tasks"))
        blocks.append({"kind": "busy", "start": start, "end": end})
    entries.sort()

    timeline = []
    cursor = owner.day_start_minute
    for start, end, text in entries:
        if start - cursor >= MIN_FREE_GAP_MINUTES:
            timeline.append((cursor, start, "Free time"))
        timeline.append((start, end, text))
        cursor = max(cursor, end)
    if owner.day_end_minute is not None and owner.day_end_minute - cursor >= MIN_FREE_GAP_MINUTES:
        timeline.append((cursor, owner.day_end_minute, "Free time"))

    return {
        "scheduled_count": len(plan.scheduled_items),
        "total_minutes": plan.total_minutes,
        "available_minutes": owner.available_minutes,
        "timeline": timeline,
        "blocks": blocks,
        "pet_names": [pet.name for pet in owner.pets],
        "day_start": owner.day_start_minute,
        "day_end": owner.day_end_minute,
        # Finished tasks aren't "left out", so they are not listed.
        "skipped": [
            (s.task.title, s.task.pet_name, s.reason) for s in plan.skipped_items if s.reason != "already completed"
        ],
    }


def render_schedule(view: dict) -> None:
    """Draw a schedule: a calendar of time blocks, with the same thing as a plain list beside it."""
    if view["scheduled_count"]:
        st.success(
            f"Scheduled {view['scheduled_count']} task(s) using "
            f"{view['total_minutes']} of {view['available_minutes']} minutes of task time "
            f"(gaps between tasks aren't counted)."
        )
        calendar_col, list_col = st.columns([3, 2], gap="large")
        with calendar_col:
            st.html(render_calendar_html(view["blocks"], view["pet_names"], view["day_start"], view["day_end"]))
        with list_col:
            st.markdown("**List view**")
            st.markdown(
                "\n".join(
                    f"- **{format_time(start)} – {format_time(end)}** · {text}"
                    for start, end, text in view["timeline"]
                )
            )
    else:
        st.warning("Nothing could be scheduled. Add tasks or increase your available time.")

    if view["skipped"]:
        left_out = "\n".join(
            f"- **{md_escape(title)}** ({md_escape(pet)}): {reason}" for title, pet, reason in view["skipped"]
        )
        st.warning(f"Some tasks were left out:\n\n{left_out}")


def context_markdown(owner: Owner) -> str:
    """The AI's context (see describe_context) laid out as a readable, wrapping list."""
    return describe_context(owner).replace("\n    - ", "\n  - ")


# ---- button callbacks (they run before the page re-runs) ----


def set_goal(text: str) -> None:
    """Put an example prompt into the planner box."""
    st.session_state.planner_goal = text


def ask_reset() -> None:
    st.session_state.confirm_reset = True


def cancel_reset() -> None:
    st.session_state.confirm_reset = False


def reset_app() -> None:
    """Forget everything (pets, tasks, AI result, and every widget's value)."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def ask_delete_pet(index: int) -> None:
    st.session_state.confirm_delete_pet = index


def cancel_delete_pet() -> None:
    st.session_state.pop("confirm_delete_pet", None)


def delete_pet(index: int) -> None:
    owner = st.session_state.owner
    if 0 <= index < len(owner.pets):
        pet = owner.pets[index]
        owner.remove_pet(pet)
        flash(f"Deleted {pet.name} and their tasks.")
    st.session_state.pop("confirm_delete_pet", None)


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(page_title="PawPal+", page_icon=":material/pets:", layout="wide")

# Streamlit re-runs this whole script on every click, so a plain `owner = Owner(...)`
# would be recreated empty each time. st.session_state is a dictionary that survives
# re-runs, so we only create the Owner the first time and reuse it afterwards.
if "owner" not in st.session_state:
    st.session_state.owner = Owner(
        name="", available_minutes=120, preferences=Preferences(buffer_minutes=BUFFER_MINUTES)
    )

owner = st.session_state.owner
scheduler = Scheduler(owner)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

title_col, night_col, reset_col = st.columns([5, 2, 2], vertical_alignment="center")
with title_col:
    st.title("PawPal+", icon=":material/pets:")
    st.markdown("Care plans that fit your day, for every pet.")
with night_col:
    st.toggle(":material/dark_mode: Night mode", key="night_mode")
with reset_col:
    st.button("Start over", icon=":material/restart_alt:", on_click=ask_reset, key="start_over", width="stretch")

apply_night_mode()

if st.session_state.get("confirm_reset"):
    with st.container(border=True):
        st.markdown("**Start over?** This clears all your pets, tasks and the AI result.")
        with st.container(horizontal=True):
            st.button("Yes, start over", on_click=reset_app, key="confirm_reset_yes")
            st.button("Cancel", on_click=cancel_reset, key="confirm_reset_no")

show_flashes()

with st.expander("How PawPal+ works", expanded=False):
    st.markdown(
        """
1. Set up your day: how many minutes you have, when your day starts (and optionally ends), and any busy times like work.
2. Add your pets.
3. Describe what you need and the **AI planner** adds tasks for you, or add tasks by hand.
4. The schedule below updates as you go. It picks high-priority tasks first, places each at its
   preferred time when that is free, and explains anything it had to leave out.
5. Completing a daily or weekly task automatically creates the next one.
"""
    )

# ---------------------------------------------------------------------------
# Top row: your day (left) and your pets (right)
# ---------------------------------------------------------------------------

day_col, pets_col = st.columns(2, gap="large")

with day_col:
    st.header("Your day")
    owner.name = st.text_input("Pet Owner", value=owner.name, placeholder="Name")
    owner.available_minutes = int(
        st.number_input(
            "Time I can spend on pet care today (minutes)",
            min_value=1,
            max_value=1440,
            value=owner.available_minutes,
            help="The total minutes you can give. Tasks must fit within this and within your day hours.",
        )
    )

    start_col, end_col = st.columns(2)
    with start_col:
        owner.day_start_minute = time_picker("Day starts at", owner.day_start_minute, key="day_start")
    with end_col:
        day_end = time_picker("Day ends at (optional)", owner.day_end_minute, key="day_end", none_label="No end time")
    if day_end is not None and day_end <= owner.day_start_minute:
        owner.day_end_minute = None
        st.warning("The day must end after it starts.")
    else:
        owner.day_end_minute = day_end

    with st.expander("Busy times (optional), e.g. work 9 AM - 5 PM", expanded=bool(owner.busy_blocks)):
        st.markdown("No tasks are scheduled during a busy time, and the AI planner avoids them too.")
        with st.form("add_busy_form"):
            busy_start_col, busy_end_col = st.columns(2)
            with busy_start_col:
                busy_start = time_picker("Busy from", 9 * 60, key="busy_start")
            with busy_end_col:
                busy_end = time_picker("Busy until", 17 * 60, key="busy_end")
            add_busy_clicked = st.form_submit_button("Add busy time")
        if add_busy_clicked:
            try:
                owner.add_busy_block(busy_start, busy_end)
            except ValueError as error:
                st.warning(str(error))
        for block in list(owner.busy_blocks):
            label = f"Delete busy time {format_time(block[0])} - {format_time(block[1])}"
            if st.button(label, icon=":material/delete:", key=f"remove_busy_{block[0]}_{block[1]}"):
                owner.remove_busy_block(block)
                st.rerun()

with pets_col:
    st.header("Your pets")
    if not owner.pets:
        st.info("Add your first pet to get started. Then you can plan their care with AI or add tasks by hand.")

    with st.form("add_pet_form", clear_on_submit=True):
        pet_name_col, species_col = st.columns([3, 2])
        pet_name = pet_name_col.text_input("Pet name")
        species = species_col.selectbox("Species", ["dog", "cat", "other"], format_func=str.title)
        add_pet_clicked = st.form_submit_button("Add pet", icon=":material/add:")

    if add_pet_clicked:
        if pet_name.strip():
            # Owner.add_pet stores the new Pet inside the Owner kept in session_state.
            owner.add_pet(Pet(name=pet_name.strip(), species=species))
            flash(f"Added {pet_name.strip()}.")
            st.rerun()
        else:
            st.warning("Please enter a pet name.")

    for index, pet in enumerate(owner.pets):
        with st.container(border=True):
            info_col, action_col = st.columns([3, 2], vertical_alignment="center")
            color = SPECIES_BADGE_COLORS.get(pet.species, "gray")
            pending_count = len([t for t in pet.tasks if not t.completed])
            info_col.markdown(
                f":material/pets: **{md_escape(pet.name)}** :{color}-badge[{pet.species.title()}]  \n"
                f"{len(pet.tasks)} task(s), {pending_count} to do"
            )
            if st.session_state.get("confirm_delete_pet") == index:
                action_col.markdown(f"Delete **{md_escape(pet.name)}** and {len(pet.tasks)} task(s)?")
                with action_col.container(horizontal=True):
                    st.button("Yes, delete", on_click=delete_pet, args=(index,), key=f"confirm_delete_pet_yes_{index}")
                    st.button("Cancel", on_click=cancel_delete_pet, key=f"confirm_delete_pet_no_{index}")
            else:
                action_col.button(
                    f"Delete {pet.name}",
                    icon=":material/delete:",
                    on_click=ask_delete_pet,
                    args=(index,),
                    key=f"delete_pet_{index}",
                    width="stretch",
                )

# ---------------------------------------------------------------------------
# Below: everything else, top to bottom
# ---------------------------------------------------------------------------

if not owner.pets:
    with st.container(border=True):
        st.markdown("### Your schedule will appear here")
        st.markdown("Add a pet above, then ask the AI planner for a routine or add tasks yourself.")
    st.stop()

# ---- AI planner ----
st.header("Plan with AI")
st.markdown(
    "Describe a goal and the AI proposes tasks for your pets. PawPal+ then checks them for time "
    "conflicts and available minutes, and sends any problems back to the AI to fix before adding anything."
)
context_col, goal_col = st.columns(2, gap="large")
with context_col:
    with st.container(border=True):
        st.markdown("**The AI is automatically given this about you** (change it in the sections above):")
        st.markdown(context_markdown(owner))

with goal_col:
    first_pet = owner.pets[0].name
    examples = {
        "New pet routine": f"Create a daily care routine for {first_pet}: feeding, play, and a short grooming session.",
        "Weekly health check": f"Add a weekly nail trim and weigh-in for {first_pet}, plus daily feeding.",
        "Busy workday": f"Plan a light care routine for {first_pet} that fits around my busy times.",
    }
    st.markdown("Try an example:")
    with st.container(horizontal=True):
        for label, text in examples.items():
            st.button(label, key=f"example_{label}", on_click=set_goal, args=(text,))

    goal = st.text_area(
        "What should PawPal+ plan?",
        key="planner_goal",
        placeholder="I just adopted a puppy, set up a daily care routine.",
    )
    if st.button("Plan with AI", icon=":material/auto_awesome:", width="stretch"):
        try:
            with st.spinner("Planning, checking, and revising..."):
                result = PlannerAgent(make_client()).run(owner, goal)
        except AgentError as error:
            st.session_state.pop("agent_result", None)
            st.error(str(error))
        else:
            st.session_state.agent_result = result
            flash(f"AI planner added {len(result.tasks)} task(s).")
            # Re-run so the schedule and task list show the new tasks. The result is kept in
            # session_state, so it is still displayed (and after later clicks).
            st.rerun()

agent_result = st.session_state.get("agent_result")
if agent_result:
    if agent_result.success and not agent_result.tasks:
        st.info("Nothing new to add: those tasks already exist.")
    elif agent_result.success:
        st.success(
            f"Added {len(agent_result.tasks)} task(s) after {agent_result.attempts} attempt(s). All checks passed."
        )
    else:
        st.warning("Tasks were added, but some problems remain. Review the conflicts under your tasks.")
        for issue in agent_result.issues:
            st.write(f"- {issue}")
    if agent_result.duplicates:
        st.markdown("Skipped because they already exist: " + "; ".join(agent_result.duplicates))
    with st.expander("What the agent did", expanded=True):
        for step in agent_result.steps:
            st.write(f"**{step.kind}**: {step.detail}")

# ---- schedule (rebuilt automatically) ----
st.header("Today's schedule")
if owner.get_all_tasks():
    # Rebuilt on every run, so it always matches the tasks and settings on screen.
    render_schedule(build_schedule_view(owner, scheduler.generate_plan()))
else:
    st.info("No tasks yet. Use the AI planner, or add one by hand below, and your schedule appears here.")

# ---- tasks, grouped by pet ----
st.header("Your tasks")
if owner.get_all_tasks():
    status = (
        st.segmented_control("Filter by status", ["All", "To do", "Done"], default="All", key="status_filter") or "All"
    )
    completed_filter = {"All": None, "To do": False, "Done": True}[status]

    shown_any = False
    for pet in owner.pets:
        # Owner.filter_tasks narrows the list; Scheduler.sort_by_time puts it in time order.
        pet_tasks = scheduler.sort_by_time(
            [t for t in owner.filter_tasks(pet_name=pet.name, completed=completed_filter) if t in pet.tasks]
        )
        if not pet_tasks:
            continue
        shown_any = True
        with st.container(border=True):
            st.markdown(f":material/pets: **{md_escape(pet.name)}** · {len(pet_tasks)} task(s)")
            st.table(
                [
                    {
                        "Time": format_time(t.preferred_time) if t.preferred_time is not None else "Any time",
                        "Task": md_escape(t.title),
                        "Minutes": t.duration_minutes,
                        "Priority": PRIORITY_BADGES[t.priority],
                        "Repeats / due": f"{t.frequency.value.title()}, due "
                        + ("today" if t.due_date <= date.today() else f"{t.due_date:%b %d}"),
                        "Status": ":green-badge[Done]" if t.completed else "To do",
                    }
                    for t in pet_tasks
                ],
                hide_index=True,
            )
    if not shown_any:
        st.info("No tasks match this filter.")

    # Tell the owner about clashes now, while they can still fix them.
    conflicts = scheduler.detect_conflicts()
    if conflicts:
        for message in conflicts:
            st.warning(message)
        st.markdown(
            "To fix a conflict, change one task's time. If you leave it, the scheduler keeps the "
            "higher-priority task at its time and moves the other to the next free slot."
        )
    else:
        st.success("No time conflicts between your tasks.")
else:
    st.info("No tasks yet. Use the AI planner, or add one below.")

st.subheader("Manage tasks")
add_tab, done_tab, edit_tab = st.tabs(["Add a task", "Mark done", "Edit or delete"])

with add_tab:
    # Select by position, then look the pet up in owner.pets. Streamlit hands back a
    # *copy* of objects passed to selectbox, and a task added to a copy would be lost.
    # The pet picker is outside the form so changing it updates the suggested title at once.
    pet_index = st.selectbox("Add task for", range(len(owner.pets)), format_func=lambda i: owner.pets[i].name)
    selected_pet = owner.pets[pet_index]

    with st.form("add_task_form"):
        title_field, minutes_field = st.columns([3, 1])
        # A different key per species gives each species its own starting title.
        task_title = title_field.text_input(
            "New task title",
            value=DEFAULT_TASK_TITLES.get(selected_pet.species, "Daily care"),
            key=f"task_title_{selected_pet.species}",
        )
        duration = minutes_field.number_input("New task minutes", min_value=1, max_value=240, value=20)
        priority_field, often_field, time_field = st.columns(3)
        priority = priority_field.selectbox(
            "New task priority",
            list(Priority),
            index=2,
            format_func=lambda p: p.name.title(),
            help="High priority tasks are scheduled first when time is short.",
        )
        frequency = often_field.selectbox(
            "How often",
            list(Frequency),
            format_func=lambda f: f.value.title(),
            help="Daily and weekly tasks create the next one when you complete them.",
        )
        with time_field:
            preferred_time = time_picker("New task preferred time", None, key="task_time", none_label="Any time")
        add_task_clicked = st.form_submit_button("Add task", icon=":material/add:")

    if add_task_clicked:
        # Pet.add_task stores the Task on the selected pet (and records the pet's name on it).
        selected_pet.add_task(Task(task_title, int(duration), priority, frequency, preferred_time=preferred_time))
        flash(f"Added '{task_title}' for {selected_pet.name}.")
        st.rerun()

with done_tab:
    pending = [(pet, task) for pet in owner.pets for task in pet.tasks if not task.completed]
    if pending:
        pending_index = st.selectbox(
            "Task to mark done",
            range(len(pending)),
            format_func=lambda i: f"{pending[i][1].title} ({pending[i][0].name})",
        )
        if st.button("Mark complete", icon=":material/check:"):
            pet, task = pending[pending_index]
            # Pet.complete_task also adds the next occurrence if the task repeats.
            next_task = pet.complete_task(task)
            if next_task:
                flash(
                    f"Done! '{task.title}' repeats {task.frequency.value}, "
                    f"next one is due {next_task.due_date:%b %d}."
                )
            else:
                flash(f"Done! '{task.title}' is complete.")
            st.rerun()
    else:
        st.markdown("Nothing left to do. 🎉")

with edit_tab:
    all_tasks = [(pet, task) for pet in owner.pets for task in pet.tasks]
    if all_tasks:
        # Pick by position (see the note above about Streamlit copying objects).
        edit_index = st.selectbox(
            "Task to edit",
            range(len(all_tasks)),
            format_func=lambda i: f"{all_tasks[i][1].title} ({all_tasks[i][0].name})"
            + (" - done" if all_tasks[i][1].completed else ""),
            key=f"edit_pick_{len(all_tasks)}",
        )
        edit_pet, edit_task = all_tasks[edit_index]
        # The form is keyed by the task so its fields reset to the chosen task's values.
        with st.form(f"edit_form_{edit_task.id}"):
            edit_title_field, edit_minutes_field = st.columns([3, 1])
            new_title = edit_title_field.text_input("Edit title", value=edit_task.title)
            new_duration = edit_minutes_field.number_input(
                "Edit minutes", min_value=1, max_value=240, value=min(edit_task.duration_minutes, 240)
            )
            edit_priority_field, edit_often_field, edit_time_field = st.columns(3)
            new_priority = edit_priority_field.selectbox(
                "Edit priority",
                list(Priority),
                index=list(Priority).index(edit_task.priority),
                format_func=lambda p: p.name.title(),
            )
            new_frequency = edit_often_field.selectbox(
                "Edit how often",
                list(Frequency),
                index=list(Frequency).index(edit_task.frequency),
                format_func=lambda f: f.value.title(),
            )
            with edit_time_field:
                new_time = time_picker(
                    "Edit preferred time", edit_task.preferred_time, key=f"edit_time_{edit_task.id}", none_label="Any time"
                )
            save_clicked = st.form_submit_button("Save changes", icon=":material/save:")
            delete_clicked = st.form_submit_button("Delete task", icon=":material/delete:")

        if save_clicked:
            edit_pet.edit_task(
                edit_task,
                title=new_title.strip() or edit_task.title,
                duration_minutes=int(new_duration),
                priority=new_priority,
                frequency=new_frequency,
                preferred_time=new_time,
            )
            flash(f"Saved changes to '{edit_task.title}'.")
            st.rerun()
        if delete_clicked:
            edit_pet.remove_task(edit_task)
            flash(f"Deleted '{edit_task.title}'.")
            st.rerun()
    else:
        st.markdown("No tasks to edit yet.")
