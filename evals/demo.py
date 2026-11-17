"""End-to-end demo of the PawPal+ planning agent, printed as plain text.

Runs the agent on three example goals and prints, for each one: the input, the agent's
trace (plan, validate, check, revise, commit) and the resulting schedule.

    python -m evals.demo              # offline: scripted model, no API key, same output every time
    python -m evals.demo --live       # real model (needs GEMINI_API_KEY or ANTHROPIC_API_KEY)

The offline mode replays the same fixed replies the eval uses, so the output can be
reproduced and compared. Only --live shows the real model's own answers.
"""

from __future__ import annotations

import argparse
import sys

from evals.run_eval import SCENARIOS, ScriptedLLM
from pawpal_agent import AgentError, PlannerAgent, make_client
from pawpal_system import Owner, Pet, Scheduler, format_time

# One scenario per behavior worth showing: self-correcting an overlap, a time budget, a guardrail.
DEMO_SCENARIOS = ("new puppy", "two pets, tight day", "unknown pet guardrail")


def run_demo(live: bool) -> None:
    by_name = {scenario.name: scenario for scenario in SCENARIOS}
    for number, name in enumerate(DEMO_SCENARIOS, start=1):
        scenario = by_name[name]
        owner = Owner("Demo", scenario.available_minutes)
        for pet_name, species in scenario.pets:
            owner.add_pet(Pet(pet_name, species))

        print(f"=== Example {number}: {scenario.name} ===")
        print(f"Input : {scenario.goal}")
        print(f"Pets  : {', '.join(f'{n} ({s})' for n, s in scenario.pets)}; {scenario.available_minutes} minutes available")
        llm = make_client() if live else ScriptedLLM(scenario.scripted_replies)
        try:
            result = PlannerAgent(llm).run(owner, scenario.goal)
        except AgentError as exc:
            print(f"Agent error: {exc} (your data was left unchanged)\n")
            continue

        print("Agent trace:")
        for step in result.steps:
            print(f"  [{step.kind}] {step.detail}")
        print(f"Result: committed={result.committed}, attempts={result.attempts}, issues left={len(result.issues)}")
        print("Schedule:")
        for item in Scheduler(owner).generate_plan().scheduled_items:
            print(f"  {format_time(item.start_minute)} - {format_time(item.end_minute)}  {item.task.title} ({item.task.pet_name})")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show the PawPal+ planning agent end to end.")
    parser.add_argument("--live", action="store_true", help="use the real model instead of the scripted one")
    run_demo(parser.parse_args().live)
    sys.exit(0)
