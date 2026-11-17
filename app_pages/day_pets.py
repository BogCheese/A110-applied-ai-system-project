"""Day & pets page: when the owner's day starts and ends, busy times, and the pets."""

import streamlit as st

from pawpal_system import Pet, format_time
from ui import SPECIES_BADGE_COLORS, flash, md_escape, time_picker

owner = st.session_state.owner


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


day_col, pets_col = st.columns(2, gap="large")

with day_col:
    st.header("Your day", divider="green")
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
        day_end = time_picker("Day ends at (optional)", owner.day_end_minute, key="day_end", optional=True)
    if day_end is not None and day_end <= owner.day_start_minute:
        owner.day_end_minute = None
        st.warning("The day must end after it starts.")
    else:
        owner.day_end_minute = day_end

    with st.expander("Busy times (optional), e.g. work 9 AM - 5 PM", expanded=bool(owner.busy_blocks)):
        st.markdown("No tasks are scheduled during a busy time, and the AI planner avoids them too.")
        with st.form("add_busy_form", border=False):
            busy_start_col, busy_end_col = st.columns(2)
            with busy_start_col:
                busy_start = time_picker("Busy from", 9 * 60, key="busy_start")
            with busy_end_col:
                busy_end = time_picker("Busy until", 17 * 60, key="busy_end")
            add_busy_clicked = st.form_submit_button("Add busy time", icon=":material/add:")
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
    st.header("Your pets", divider="green")
    if not owner.pets:
        st.info("Add your first pet to get started. Then you can plan their care with AI or add tasks by hand.")

    with st.container(border=True):
        with st.form("add_pet_form", clear_on_submit=True, border=False):
            pet_name_col, species_col = st.columns([3, 2])
            pet_name = pet_name_col.text_input("Pet name")
            species = species_col.segmented_control(
                "Species", ["dog", "cat", "other"], default="dog", required=True, format_func=str.title
            )
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
