"""PawPal+ entry point: shared header, the top navigation bar, and the page that is open.

The pages themselves live in app_pages/ and the shared helpers in ui.py. Run with:
    streamlit run app.py
"""

import streamlit as st

from pawpal_agent import setup_logging
from pawpal_system import Owner, Preferences
from ui import BUFFER_MINUTES, apply_night_mode, ask_reset, cancel_reset, reset_app, show_flashes

setup_logging()

st.set_page_config(page_title="PawPal+", page_icon=":material/pets:", layout="wide")

# Streamlit re-runs this whole script on every click, so a plain `owner = Owner(...)`
# would be recreated empty each time. st.session_state is a dictionary that survives
# re-runs (and is shared by every page), so we only create the Owner the first time.
if "owner" not in st.session_state:
    st.session_state.owner = Owner(
        name="", available_minutes=120, preferences=Preferences(buffer_minutes=BUFFER_MINUTES)
    )

page = st.navigation(
    [
        st.Page(
            "app_pages/schedule.py",
            title="Schedule",
            icon=":material/calendar_today:",
            default=True,
            help="Today's calendar and list, every task grouped by pet, and a form to add tasks.",
        ),
        st.Page(
            "app_pages/plan_ai.py",
            title="Plan with AI",
            icon=":material/auto_awesome:",
            help="Describe a goal and the AI proposes tasks, checks them, and adds them.",
        ),
        st.Page(
            "app_pages/day_pets.py",
            title="Day & pets",
            icon=":material/pets:",
            help="Set your day, busy times and available minutes, and add or remove pets.",
        ),
    ],
    position="top",
)

# ---------------------------------------------------------------------------
# Header, shown on every page
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
1. On **Day & pets**, set how many minutes you have, when your day starts (and optionally ends), and any busy times like work. Then add your pets.
2. On **Plan with AI**, describe what you need and the AI planner adds tasks for you, or add tasks by hand on **Schedule**.
3. The **Schedule** updates as you go. It picks high-priority tasks first, places each at its
   preferred time when that is free, and explains anything it had to leave out.
4. Tick a task's checkbox when it is done, or press Edit to change it. Completing a daily or weekly task
   automatically creates the next one.
"""
    )

page.run()
