"""Schedule page: today's calendar and list, every task grouped by pet, and the Add a task form."""

from datetime import date

import streamlit as st

from pawpal_system import Frequency, Priority, Scheduler, Task, format_time
from ui import (
    DEFAULT_TASK_TITLES,
    PRIORITY_BADGES,
    build_schedule_view,
    flash,
    md_escape,
    render_schedule,
    show_editor_if_open,
    time_picker,
)

owner = st.session_state.owner
scheduler = Scheduler(owner)

if not owner.pets:
    with st.container(border=True):
        st.markdown("### Your schedule will appear here")
        st.markdown("Add a pet first, then ask the AI planner for a routine or add tasks yourself.")
        st.page_link("app_pages/day_pets.py", label="Add your first pet", icon=":material/pets:")
    st.stop()

# ---- schedule (rebuilt automatically) ----
st.header("Today's schedule", divider="green")
if owner.get_all_tasks():
    # Rebuilt on every run, so it always matches the tasks and settings on screen.
    render_schedule(build_schedule_view(owner, scheduler.generate_plan()))
else:
    st.info("No tasks yet. Use the AI planner, or add one by hand below, and your schedule appears here.")
    st.page_link("app_pages/plan_ai.py", label="Plan with AI", icon=":material/auto_awesome:")

# ---- tasks, grouped by pet ----
st.header("Your tasks", divider="green")
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

# ---- add a task ----
st.subheader("Add a task", divider="green")
# Select by position, then look the pet up in owner.pets. Streamlit hands back a
# *copy* of objects passed to selectbox, and a task added to a copy would be lost.
# The pet picker is outside the form so changing it updates the suggested title at once.
pet_index = st.selectbox("Add task for", range(len(owner.pets)), format_func=lambda i: owner.pets[i].name)
selected_pet = owner.pets[pet_index]

with st.container(border=True):
    with st.form("add_task_form", border=False):
        title_field, minutes_field = st.columns([3, 1])
        # A different key per species gives each species its own starting title.
        task_title = title_field.text_input(
            "New task title",
            value=DEFAULT_TASK_TITLES.get(selected_pet.species, "Daily care"),
            key=f"task_title_{selected_pet.species}",
        )
        duration = minutes_field.number_input("New task minutes", min_value=1, max_value=240, value=20)
        priority_field, often_field, time_field = st.columns(3)
        priority = priority_field.segmented_control(
            "New task priority",
            list(Priority),
            default=Priority.HIGH,
            required=True,
            format_func=lambda p: p.name.title(),
            help="High priority tasks are scheduled first when time is short.",
        )
        frequency = often_field.segmented_control(
            "How often",
            list(Frequency),
            default=Frequency.ONCE,
            required=True,
            format_func=lambda f: f.value.title(),
            help="Daily and weekly tasks create the next one when you complete them.",
        )
        with time_field:
            preferred_time = time_picker("New task preferred time (optional)", None, key="task_time", optional=True)
        add_task_clicked = st.form_submit_button("Add task", icon=":material/add:")

if add_task_clicked:
    clean_title = task_title.strip()
    if not clean_title:
        st.warning("Please give the task a title.")
    else:
        # Pet.add_task stores the Task on the selected pet (and records the pet's name on it).
        selected_pet.add_task(Task(clean_title, int(duration), priority, frequency, preferred_time=preferred_time))
        flash(f"Added '{clean_title}' for {selected_pet.name}.")
        st.rerun()

# The edit window opens here when an Edit button above was pressed.
show_editor_if_open(owner)
