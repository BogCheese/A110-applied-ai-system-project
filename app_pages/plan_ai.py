"""Plan with AI page: describe a goal and the agent proposes, checks, and adds tasks."""

import streamlit as st

import pawpal_agent
from pawpal_agent import AgentError, PlannerAgent
from ui import context_markdown, flash

owner = st.session_state.owner


def set_goal(text: str) -> None:
    """Put an example prompt into the planner box."""
    st.session_state.planner_goal = text


st.header("Plan with AI", divider="green")

if not owner.pets:
    with st.container(border=True):
        st.markdown("### Add a pet first")
        st.markdown("The AI plans care for your pets, so add one before asking for a routine.")
        st.page_link("app_pages/day_pets.py", label="Go to Day & pets", icon=":material/pets:")
    st.stop()

st.markdown(
    "Describe a goal and the AI proposes tasks for your pets. PawPal+ then checks them for time "
    "conflicts and available minutes, and sends any problems back to the AI to fix before adding anything."
)
context_col, goal_col = st.columns(2, gap="large")
with context_col:
    with st.container(border=True):
        st.markdown("**The AI is automatically given this about you** (change it on the Day & pets page):")
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
                result = PlannerAgent(pawpal_agent.make_client()).run(owner, goal)
        except AgentError as error:
            st.session_state.pop("agent_result", None)
            st.error(str(error))
        else:
            st.session_state.agent_result = result
            flash(f"AI planner added {len(result.tasks)} task(s).")
            # Re-run so the context panel shows the new tasks. The result is kept in
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
        st.warning("Tasks were added, but some problems remain. Review the conflicts on the Schedule page.")
        for issue in agent_result.issues:
            st.write(f"- {issue}")
    if agent_result.duplicates:
        st.markdown("Skipped because they already exist: " + "; ".join(agent_result.duplicates))
    with st.expander("What the agent did", expanded=True):
        for step in agent_result.steps:
            st.write(f"**{step.kind}**: {step.detail}")
    st.page_link("app_pages/schedule.py", label="See it on your schedule", icon=":material/calendar_today:")
