import streamlit as st

from datetime import date

from pawpal_agent import AgentError, PlannerAgent, describe_context, make_client, setup_logging
from pawpal_system import Frequency, Owner, Pet, Priority, Scheduler, Task, format_time

setup_logging()

# Starter task titles, so the form suggests something that makes sense for the pet.
DEFAULT_TASK_TITLES = {"dog": "Morning walk", "cat": "Feed breakfast"}


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


st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")

# Streamlit re-runs this whole script on every click, so a plain `owner = Owner(...)`
# would be recreated empty each time. st.session_state is a dictionary that survives
# re-runs, so we only create the Owner the first time and reuse it afterwards.
if "owner" not in st.session_state:
    st.session_state.owner = Owner(name="Jordan", available_minutes=120)

st.title("🐾 PawPal+")

st.markdown(
    """
Plan your pets' care for the day. Add your pets and their tasks, check for time
conflicts, and let PawPal+ build a schedule that fits the time you have.
"""
)

with st.expander("How PawPal+ works", expanded=False):
    st.markdown(
        """
1. Enter how many minutes you have today, when your day starts (and optionally ends), any busy times like work, and add your pets.
2. Give each pet tasks with a duration, priority, how often it repeats, and an optional preferred time.
3. Tasks are listed in time order, and you'll get a warning if two tasks' preferred times overlap.
4. **Generate schedule** picks high-priority tasks first, places each at its preferred time when it's free,
   and explains why each task was scheduled or left out.
5. Completing a daily or weekly task automatically creates the next one.
"""
    )

st.divider()

owner = st.session_state.owner

st.subheader("Owner")
owner.name = st.text_input("Owner name", value=owner.name)
owner.available_minutes = int(
    st.number_input("Minutes available today", min_value=1, max_value=1440, value=owner.available_minutes)
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
    st.caption("No tasks are scheduled during a busy time, and the AI planner avoids them too.")
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
        label_col, remove_col = st.columns([3, 1])
        label_col.write(f"{format_time(block[0])} - {format_time(block[1])}")
        if remove_col.button("Remove", key=f"remove_busy_{block[0]}_{block[1]}"):
            owner.remove_busy_block(block)
            st.rerun()

st.subheader("Pets")
with st.form("add_pet_form", clear_on_submit=True):
    pet_name = st.text_input("Pet name")
    species = st.selectbox("Species", ["dog", "cat", "other"], format_func=str.title)
    add_pet_clicked = st.form_submit_button("Add pet")

if add_pet_clicked:
    if pet_name.strip():
        # Owner.add_pet stores the new Pet inside the Owner kept in session_state.
        owner.add_pet(Pet(name=pet_name.strip(), species=species))
    else:
        st.warning("Please enter a pet name.")

# Streamlit re-runs the script after the form is submitted, so this list is rebuilt
# from owner.pets and the new pet shows up right away.
if owner.pets:
    st.table(
        [{"Pet": pet.name, "Species": pet.species.title(), "Tasks": len(pet.tasks)} for pet in owner.pets]
    )
else:
    st.info("No pets yet. Add one above.")

st.markdown("### Tasks")
scheduler = Scheduler(owner)

if not owner.pets:
    st.caption("Add a pet first, then you can give it tasks.")
else:
    # Select by position, then look the pet up in owner.pets. Streamlit hands back a
    # *copy* of objects passed to selectbox, and a task added to a copy would be lost.
    pet_index = st.selectbox(
        "Pet", range(len(owner.pets)), format_func=lambda i: owner.pets[i].name
    )
    selected_pet = owner.pets[pet_index]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        # A different key per species gives each species its own starting title.
        task_title = st.text_input(
            "Task title",
            value=DEFAULT_TASK_TITLES.get(selected_pet.species, "Daily care"),
            key=f"task_title_{selected_pet.species}",
        )
    with col2:
        duration = st.number_input("Duration (minutes)", min_value=1, max_value=240, value=20)
    with col3:
        priority = st.selectbox(
            "Priority", list(Priority), index=2, format_func=lambda p: p.name.title()
        )
    with col4:
        frequency = st.selectbox(
            "Frequency", list(Frequency), format_func=lambda f: f.value.title()
        )

    preferred_time = time_picker("Preferred time", None, key="task_time", none_label="Any time")

    if st.button("Add task"):
        # Pet.add_task stores the Task on the selected pet (and records the pet's name on it).
        selected_pet.add_task(
            Task(task_title, int(duration), priority, frequency, preferred_time=preferred_time)
        )

    # This sits above the task table so a completed task shows as done on the same click.
    pending = [(pet, task) for pet in owner.pets for task in pet.tasks if not task.completed]
    if pending:
        st.markdown("#### Mark a task complete")
        pending_index = st.selectbox(
            "Task",
            range(len(pending)),
            format_func=lambda i: f"{pending[i][1].title} ({pending[i][0].name})",
        )
        if st.button("Mark complete"):
            pet, task = pending[pending_index]
            # Pet.complete_task also adds the next occurrence if the task repeats.
            next_task = pet.complete_task(task)
            if next_task:
                st.success(
                    f"Done! '{task.title}' repeats {task.frequency.value}, "
                    f"so the next one is due {next_task.due_date:%b %d}."
                )
            else:
                st.success(f"Done! '{task.title}' is complete.")

if owner.get_all_tasks():
    st.markdown("#### Your tasks")
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
                    "Frequency": task.frequency.value.title(),
                    "Due": "Today" if task.due_date <= date.today() else f"{task.due_date:%b %d}",
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
        st.caption(
            "To fix a conflict, change one task's time. If you leave it, the scheduler keeps the "
            "higher-priority task at its time and moves the other to the next free slot."
        )
    else:
        st.success("No time conflicts between your tasks.")

    with st.expander("Edit or delete a task"):
        # Pick by position (see the note above about Streamlit copying objects).
        all_tasks = [(pet, task) for pet in owner.pets for task in pet.tasks]
        edit_index = st.selectbox(
            "Task",
            range(len(all_tasks)),
            format_func=lambda i: f"{all_tasks[i][1].title} ({all_tasks[i][0].name})"
            + (" - done" if all_tasks[i][1].completed else ""),
            key=f"edit_pick_{len(all_tasks)}",
        )
        edit_pet, edit_task = all_tasks[edit_index]
        # The form is keyed by the task so its fields reset to the chosen task's values.
        with st.form(f"edit_form_{edit_task.id}"):
            new_title = st.text_input("Title", value=edit_task.title)
            edit_cols = st.columns(3)
            new_duration = edit_cols[0].number_input(
                "Duration (minutes)", min_value=1, max_value=240, value=min(edit_task.duration_minutes, 240)
            )
            new_priority = edit_cols[1].selectbox(
                "Priority",
                list(Priority),
                index=list(Priority).index(edit_task.priority),
                format_func=lambda p: p.name.title(),
            )
            new_frequency = edit_cols[2].selectbox(
                "Frequency",
                list(Frequency),
                index=list(Frequency).index(edit_task.frequency),
                format_func=lambda f: f.value.title(),
            )
            new_time = time_picker(
                "Preferred time", edit_task.preferred_time, key=f"edit_time_{edit_task.id}", none_label="Any time"
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
            st.rerun()
        if delete_clicked:
            edit_pet.remove_task(edit_task)
            st.rerun()
else:
    st.info("No tasks yet. Add one above.")

st.divider()

st.subheader("AI Planner")
st.caption(
    "Describe a goal and the AI proposes tasks for your pets. PawPal+ then checks them for time "
    "conflicts and available minutes, and sends any problems back to the AI to fix before adding anything."
)
if not owner.pets:
    st.caption("Add a pet first.")
else:
    # Show the user exactly what the AI will be told besides their request.
    with st.container(border=True):
        st.caption("The AI is automatically given this about you (change it in the sections above):")
        st.text(describe_context(owner))
    goal = st.text_area("What should PawPal+ plan?", placeholder="I just adopted a puppy, set up a daily care routine.")
    if st.button("Plan with AI"):
        try:
            with st.spinner("Planning, checking, and revising..."):
                st.session_state.agent_result = PlannerAgent(make_client()).run(owner, goal)
        except AgentError as error:
            st.session_state.pop("agent_result", None)
            st.error(str(error))
        else:
            # Re-run so the task list above shows the new tasks. The result is kept in
            # session_state, so it is still displayed below (and after later clicks).
            st.rerun()

    agent_result = st.session_state.get("agent_result")
    if agent_result:
        if agent_result.success and not agent_result.tasks:
            st.info("Nothing new to add: those tasks already exist.")
        elif agent_result.success:
            st.success(f"Added {len(agent_result.tasks)} task(s) after {agent_result.attempts} attempt(s). All checks passed.")
        else:
            st.warning("Tasks were added, but some problems remain. Review the conflicts above.")
            for issue in agent_result.issues:
                st.write(f"- {issue}")
        if agent_result.duplicates:
            st.caption("Skipped because they already exist: " + "; ".join(agent_result.duplicates))
        with st.expander("What the agent did", expanded=True):
            for step in agent_result.steps:
                st.write(f"**{step.kind}**: {step.detail}")

st.divider()

st.subheader("Build Schedule")
st.caption("Builds today's plan from every pet's tasks: highest priority first, within your available minutes.")

if st.button("Generate schedule"):
    plan = scheduler.generate_plan()

    if plan.scheduled_items:
        st.success(
            f"Scheduled {len(plan.scheduled_items)} task(s) using "
            f"{plan.total_minutes} of {owner.available_minutes} minutes."
        )
        st.table(
            [
                {
                    "Start": format_time(item.start_minute),
                    "End": format_time(item.end_minute),
                    "Task": item.task.title,
                    "Pet": item.task.pet_name,
                    "Why": item.reason,
                }
                for item in plan.scheduled_items
            ]
        )
    else:
        st.warning("Nothing could be scheduled. Add tasks or increase your available minutes.")

    if plan.skipped_items:
        left_out = "\n".join(
            f"- **{skipped.task.title}** ({skipped.task.pet_name}): {skipped.reason}"
            for skipped in plan.skipped_items
        )
        st.warning(f"Some tasks were left out:\n\n{left_out}")
