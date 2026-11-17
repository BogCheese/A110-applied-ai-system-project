import streamlit as st

from datetime import date

from pawpal_agent import AgentError, PlannerAgent, describe_context, make_client, setup_logging
from pawpal_system import Frequency, Owner, Pet, Preferences, Priority, Scheduler, Task, format_time

setup_logging()

# Gap left between consecutive tasks (travel, cleanup, a breather). Not user-editable.
BUFFER_MINUTES = 5

# Starter task titles, so the form suggests something that makes sense for the pet.
DEFAULT_TASK_TITLES = {"dog": "Morning walk", "cat": "Feed breakfast"}

PRIORITY_ICONS = {Priority.HIGH: "🔴", Priority.MEDIUM: "🟡", Priority.LOW: "🟢"}

# Free gaps shorter than this are not worth listing in the timeline.
MIN_FREE_GAP_MINUTES = 10


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


def flash(message: str) -> None:
    """Queue a small pop-up message to show after the page re-runs."""
    st.session_state.setdefault("flash", []).append(message)


def show_flashes() -> None:
    """Show (and clear) the queued pop-up messages."""
    for message in st.session_state.pop("flash", []):
        st.toast(message)


def snapshot(owner: Owner) -> tuple:
    """Everything a schedule depends on, so we can tell when a saved schedule is out of date."""
    return (
        owner.available_minutes,
        owner.day_start_minute,
        owner.day_end_minute,
        tuple(owner.busy_blocks),
        owner.preferences.buffer_minutes,
        tuple(
            (t.id, t.title, t.duration_minutes, t.priority, t.frequency, t.preferred_time, t.completed, t.due_date)
            for t in owner.get_all_tasks()
        ),
    )


def build_schedule_view(owner: Owner, plan) -> dict:
    """Turn a DailyPlan into plain data to keep in session_state and draw later.

    Plain data (not the live plan) means the saved schedule stays exactly as it was
    built, even if tasks are edited afterwards.
    """
    entries = []  # (start, end, text)
    for item in plan.scheduled_items:
        task = item.task
        entries.append(
            (
                item.start_minute,
                item.end_minute,
                f"{task.title} ({task.pet_name}) · {PRIORITY_ICONS[task.priority]} {task.priority.name.title()} priority"
                f" — {item.reason.split(', ', 1)[-1]}",
            )
        )
    for start, end in owner.busy_blocks:
        entries.append((start, end, "🚫 Busy, no tasks"))
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
        "snapshot": snapshot(owner),
        "scheduled_count": len(plan.scheduled_items),
        "total_minutes": plan.total_minutes,
        "available_minutes": owner.available_minutes,
        "timeline": timeline,
        "skipped": [(s.task.title, s.task.pet_name, s.reason) for s in plan.skipped_items],
    }


def render_schedule(view: dict) -> None:
    """Draw a saved schedule as a time-ordered list."""
    if view["scheduled_count"]:
        st.success(
            f"Scheduled {view['scheduled_count']} task(s) using "
            f"{view['total_minutes']} of {view['available_minutes']} minutes."
        )
        st.markdown(
            "\n".join(
                f"- **{format_time(start)} – {format_time(end)}** · {text}" for start, end, text in view["timeline"]
            )
        )
    else:
        st.warning("Nothing could be scheduled. Add tasks or increase your available time.")

    if view["skipped"]:
        left_out = "\n".join(f"- **{title}** ({pet}): {reason}" for title, pet, reason in view["skipped"])
        st.warning(f"Some tasks were left out:\n\n{left_out}")


def context_markdown(owner: Owner) -> str:
    """The AI's context (see describe_context) laid out as a readable, wrapping list."""
    return describe_context(owner).replace("\n    - ", "\n  - ")


def set_goal(text: str) -> None:
    """Button callback: put an example prompt into the planner box."""
    st.session_state.planner_goal = text


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")

# Streamlit re-runs this whole script on every click, so a plain `owner = Owner(...)`
# would be recreated empty each time. st.session_state is a dictionary that survives
# re-runs, so we only create the Owner the first time and reuse it afterwards.
if "owner" not in st.session_state:
    st.session_state.owner = Owner(
        name="Jordan", available_minutes=120, preferences=Preferences(buffer_minutes=BUFFER_MINUTES)
    )

owner = st.session_state.owner

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("🐾 PawPal+")
st.markdown(
    "Plan your pets' care for the day. Add your pets, tell the AI what you need or add tasks "
    "yourself, and PawPal+ builds a schedule that fits your day."
)
show_flashes()

with st.expander("How PawPal+ works", expanded=False):
    st.markdown(
        """
1. Set up your day: how many minutes you have, when your day starts (and optionally ends), and any busy times like work.
2. Add your pets.
3. Describe what you need and the **AI planner** adds tasks for you, or add tasks by hand.
4. Tasks are listed in time order, with a warning if two tasks' preferred times overlap.
5. **Generate schedule** picks high-priority tasks first, places each at its preferred time when it's free,
   and explains why each task was scheduled or left out.
6. Completing a daily or weekly task automatically creates the next one.
"""
    )

# ---------------------------------------------------------------------------
# 1. Your day
# ---------------------------------------------------------------------------

st.header("1. Your day")
owner.name = st.text_input("Owner name", value=owner.name)
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
    st.markdown(
        "No tasks are scheduled during a busy time, and the AI planner avoids them too."
    )
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
        if st.button(label, key=f"remove_busy_{block[0]}_{block[1]}"):
            owner.remove_busy_block(block)
            st.rerun()

# ---------------------------------------------------------------------------
# 2. Pets
# ---------------------------------------------------------------------------

scheduler = Scheduler(owner)

if owner.pets:
    # A quick overview at the top. Actions below end with st.rerun(), so these are never stale.
    live_plan = scheduler.generate_plan()
    metric_cols = st.columns(4)
    metric_cols[0].metric("Pets", len(owner.pets))
    metric_cols[1].metric("Tasks to do", len([t for t in owner.get_all_tasks() if not t.completed]))
    metric_cols[2].metric("Scheduled time", f"{live_plan.total_minutes} / {owner.available_minutes} min")
    metric_cols[3].metric("Conflicts", len(scheduler.detect_conflicts()))

st.header("2. Your pets")
if not owner.pets:
    st.info("👋 Add your first pet to get started. Then you can plan their care with AI or add tasks by hand.")

with st.form("add_pet_form", clear_on_submit=True):
    pet_name = st.text_input("Pet name")
    species = st.selectbox("Species", ["dog", "cat", "other"], format_func=str.title)
    add_pet_clicked = st.form_submit_button("Add pet")

if add_pet_clicked:
    if pet_name.strip():
        # Owner.add_pet stores the new Pet inside the Owner kept in session_state.
        owner.add_pet(Pet(name=pet_name.strip(), species=species))
        flash(f"Added {pet_name.strip()}.")
        st.rerun()
    else:
        st.warning("Please enter a pet name.")

if owner.pets:
    st.table(
        [{"Pet": pet.name, "Species": pet.species.title(), "Tasks": len(pet.tasks)} for pet in owner.pets]
    )
else:
    st.stop()  # nothing else is useful until there is a pet

# ---------------------------------------------------------------------------
# 3. AI planner
# ---------------------------------------------------------------------------

st.header("3. Plan with AI")
st.markdown(
    "Describe a goal and the AI proposes tasks for your pets. PawPal+ then checks them for time "
    "conflicts and available minutes, and sends any problems back to the AI to fix before adding anything."
)
with st.container(border=True):
    st.markdown("**The AI is automatically given this about you** (change it in the sections above):")
    st.markdown(context_markdown(owner))

first_pet = owner.pets[0].name
examples = {
    "New pet routine": f"Create a daily care routine for {first_pet}: feeding, play, and a short grooming session.",
    "Weekly health check": f"Add a weekly nail trim and weigh-in for {first_pet}, plus daily feeding.",
    "Busy workday": f"Plan a light care routine for {first_pet} that fits around my busy times.",
}
st.markdown("Try an example:")
for column, (label, text) in zip(st.columns(len(examples)), examples.items()):
    column.button(label, key=f"example_{label}", on_click=set_goal, args=(text,), use_container_width=True)

goal = st.text_area(
    "What should PawPal+ plan?",
    key="planner_goal",
    placeholder="I just adopted a puppy, set up a daily care routine.",
)
if st.button("Plan with AI", icon=":material/auto_awesome:", use_container_width=True):
    try:
        with st.spinner("Planning, checking, and revising..."):
            result = PlannerAgent(make_client()).run(owner, goal)
    except AgentError as error:
        st.session_state.pop("agent_result", None)
        st.error(str(error))
    else:
        st.session_state.agent_result = result
        flash(f"AI planner added {len(result.tasks)} task(s).")
        # Re-run so the task list below shows the new tasks. The result is kept in
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
        st.warning("Tasks were added, but some problems remain. Review the conflicts below.")
        for issue in agent_result.issues:
            st.write(f"- {issue}")
    if agent_result.duplicates:
        st.markdown("Skipped because they already exist: " + "; ".join(agent_result.duplicates))
    with st.expander("What the agent did", expanded=True):
        for step in agent_result.steps:
            st.write(f"**{step.kind}**: {step.detail}")

# ---------------------------------------------------------------------------
# 4. Tasks
# ---------------------------------------------------------------------------

st.header("4. Your tasks")

if owner.get_all_tasks():
    filter_pet_col, filter_status_col = st.columns(2)
    with filter_pet_col:
        pet_filter = st.selectbox("Filter by pet", ["All pets"] + [pet.name for pet in owner.pets])
    with filter_status_col:
        status_filter = st.radio("Filter by status", ["All", "To do", "Done"], horizontal=True)

    # Owner.filter_tasks narrows the list; Scheduler.sort_by_time puts it in time order.
    filtered_tasks = owner.filter_tasks(
        pet_name=None if pet_filter == "All pets" else pet_filter,
        completed={"All": None, "To do": False, "Done": True}[status_filter],
    )
    shown_tasks = scheduler.sort_by_time(filtered_tasks)

    if shown_tasks:
        st.table(
            [
                {
                    "Time": format_time(task.preferred_time) if task.preferred_time is not None else "Any time",
                    "Pet": task.pet_name,
                    "Task": task.title,
                    "Minutes": task.duration_minutes,
                    "Priority": task.priority.name.title(),
                    "Repeats / due": f"{task.frequency.value.title()}, due "
                    + ("today" if task.due_date <= date.today() else f"{task.due_date:%b %d}"),
                    "Status": "Done" if task.completed else "To do",
                }
                for task in shown_tasks
            ]
        )
    else:
        st.info("No tasks match these filters.")

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
    st.info("No tasks yet. Use the AI planner above, or add one by hand below.")

st.subheader("Manage tasks")
add_tab, done_tab, edit_tab = st.tabs(["Add a task", "Mark done", "Edit or delete"])

with add_tab:
    # Select by position, then look the pet up in owner.pets. Streamlit hands back a
    # *copy* of objects passed to selectbox, and a task added to a copy would be lost.
    # The pet picker is outside the form so changing it updates the suggested title at once.
    pet_index = st.selectbox("Add task for", range(len(owner.pets)), format_func=lambda i: owner.pets[i].name)
    selected_pet = owner.pets[pet_index]

    with st.form("add_task_form"):
        left, right = st.columns(2)
        # A different key per species gives each species its own starting title.
        task_title = left.text_input(
            "New task title",
            value=DEFAULT_TASK_TITLES.get(selected_pet.species, "Daily care"),
            key=f"task_title_{selected_pet.species}",
        )
        duration = right.number_input("New task minutes", min_value=1, max_value=240, value=20)
        priority = left.selectbox(
            "New task priority",
            list(Priority),
            index=2,
            format_func=lambda p: p.name.title(),
            help="High priority tasks are scheduled first when time is short.",
        )
        frequency = right.selectbox(
            "How often",
            list(Frequency),
            format_func=lambda f: f.value.title(),
            help="Daily and weekly tasks create the next one when you complete them.",
        )
        preferred_time = time_picker("New task preferred time", None, key="task_time", none_label="Any time")
        add_task_clicked = st.form_submit_button("Add task")

    if add_task_clicked:
        # Pet.add_task stores the Task on the selected pet (and records the pet's name on it).
        selected_pet.add_task(
            Task(task_title, int(duration), priority, frequency, preferred_time=preferred_time)
        )
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
        if st.button("Mark complete"):
            pet, task = pending[pending_index]
            # Pet.complete_task also adds the next occurrence if the task repeats.
            next_task = pet.complete_task(task)
            if next_task:
                flash(f"Done! '{task.title}' repeats {task.frequency.value}, next one is due {next_task.due_date:%b %d}.")
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
            new_title = st.text_input("Edit title", value=edit_task.title)
            edit_left, edit_right = st.columns(2)
            new_duration = edit_left.number_input(
                "Edit minutes", min_value=1, max_value=240, value=min(edit_task.duration_minutes, 240)
            )
            new_priority = edit_right.selectbox(
                "Edit priority",
                list(Priority),
                index=list(Priority).index(edit_task.priority),
                format_func=lambda p: p.name.title(),
            )
            new_frequency = edit_left.selectbox(
                "Edit how often",
                list(Frequency),
                index=list(Frequency).index(edit_task.frequency),
                format_func=lambda f: f.value.title(),
            )
            new_time = time_picker(
                "Edit preferred time", edit_task.preferred_time, key=f"edit_time_{edit_task.id}", none_label="Any time"
            )
            save_clicked = st.form_submit_button("Save changes")
            delete_clicked = st.form_submit_button("Delete task")

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

# ---------------------------------------------------------------------------
# 5. Schedule
# ---------------------------------------------------------------------------

st.header("5. Today's schedule")
st.markdown("Builds today's plan from every pet's tasks: highest priority first, within your available time.")

if st.button("Generate schedule", icon=":material/event_available:", use_container_width=True):
    st.session_state.schedule = build_schedule_view(owner, scheduler.generate_plan())

saved_schedule = st.session_state.get("schedule")
if saved_schedule:
    if saved_schedule["snapshot"] != snapshot(owner):
        st.warning("Your tasks or settings changed since this schedule was built. Click Generate schedule to update it.")
    render_schedule(saved_schedule)
