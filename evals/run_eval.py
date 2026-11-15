"""Reliability check for the PawPal+ planning agent.

Runs the agent on a fixed set of scenarios and scores each result with checks
that use PawPal's own scheduler (not the model's say-so):

    committed      the agent produced a usable plan and added tasks
    no_conflicts   no overlapping preferred times among the pending tasks
    fits_time      every task fits inside the owner's available minutes
    covers_pets    every pet received at least one task
    has_feeding    every dog/cat received a feeding task

Each scenario is repeated --repeats times, so the report shows consistency too.

    python -m evals.run_eval              # offline: scripted fake model, no API key
    python -m evals.run_eval --live       # real model (needs GEMINI_API_KEY or ANTHROPIC_API_KEY)
    python -m evals.run_eval --live --repeats 5
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

from pawpal_agent import AgentError, PlannerAgent, make_client
from pawpal_system import Owner, Pet, Scheduler


def _tasks(*items):
    return json.dumps({"tasks": [dict(zip(("pet", "title", "duration_minutes", "priority", "frequency", "preferred_time"), i)) for i in items]})


@dataclass
class Scenario:
    name: str
    goal: str
    pets: list[tuple[str, str]]
    available_minutes: int
    scripted_replies: list[str] = field(default_factory=list)  # used by the offline fake model


SCENARIOS = [
    Scenario(
        "new puppy", "I just adopted a puppy, set up a daily care routine.",
        [("Biscuit", "dog")], 120,
        [_tasks(
            ("Biscuit", "Morning feeding", 10, "high", "daily", "08:00"),
            ("Biscuit", "Evening feeding", 10, "high", "daily", "18:00"),
            ("Biscuit", "Walk", 30, "high", "daily", "08:00"),  # overlaps the feeding -> agent must revise
        ), _tasks(
            ("Biscuit", "Morning feeding", 10, "high", "daily", "08:00"),
            ("Biscuit", "Evening feeding", 10, "high", "daily", "18:00"),
            ("Biscuit", "Walk", 30, "high", "daily", "08:30"),
        )],
    ),
    Scenario(
        "two pets, tight day", "Care routine for my cat and dog, I only have 45 minutes a day.",
        [("Mia", "cat"), ("Rex", "dog")], 45,
        [_tasks(
            ("Mia", "Feeding", 10, "high", "daily", "07:00"),
            ("Rex", "Feeding", 10, "high", "daily", "07:30"),
            ("Rex", "Long hike", 90, "medium", "daily", None),  # does not fit -> revise
        ), _tasks(
            ("Mia", "Feeding", 10, "high", "daily", "07:00"),
            ("Rex", "Feeding", 10, "high", "daily", "07:30"),
            ("Rex", "Short walk", 20, "medium", "daily", "17:00"),
        )],
    ),
    Scenario(
        "weekly grooming", "Plan feeding and weekly grooming for my cat.",
        [("Luna", "cat")], 90,
        ["Sure! Here is a plan: (not JSON)", _tasks(
            ("Luna", "Feeding", 5, "high", "daily", "08:00"),
            ("Luna", "Brushing", 15, "low", "weekly", "19:00"),
        )],
    ),
    Scenario(
        "unknown pet guardrail", "Set up feeding for my dog Max.",
        [("Max", "dog")], 60,
        [_tasks(("Maxwell", "Feeding", 10, "high", "daily", "08:00")), _tasks(("Max", "Feeding", 10, "high", "daily", "08:00"))],
    ),
]


class ScriptedLLM:
    """Offline fake model: replays a scenario's canned replies in order."""

    def __init__(self, replies: list[str]) -> None:
        self.replies, self.calls = replies, 0

    def complete(self, system: str, messages: list[dict]) -> str:
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply


def score(owner: Owner, committed: bool) -> dict[str, bool]:
    """Run every check against the owner the agent just modified."""
    scheduler = Scheduler(owner)
    plan = scheduler.generate_plan()
    return {
        "committed": committed,
        "no_conflicts": not scheduler.detect_conflicts(),
        "fits_time": not [s for s in plan.skipped_items if s.reason != "already completed"],
        "covers_pets": all(pet.tasks for pet in owner.pets),
        "has_feeding": all(
            any("feed" in t.title.lower() for t in pet.tasks)
            for pet in owner.pets if pet.species in ("dog", "cat")
        ),
    }


def run_once(scenario: Scenario, live: bool) -> tuple[dict[str, bool], int, int]:
    owner = Owner("Eval", scenario.available_minutes)
    for name, species in scenario.pets:
        owner.add_pet(Pet(name, species))
    llm = make_client() if live else ScriptedLLM(scenario.scripted_replies)
    try:
        result = PlannerAgent(llm).run(owner, scenario.goal)
        committed, attempts = result.committed, result.attempts
    except AgentError as exc:
        print(f"    agent error: {exc}")
        committed, attempts = False, 0
    return score(owner, committed), len(owner.get_all_tasks()), attempts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="use the real model instead of the scripted fake")
    parser.add_argument("--repeats", type=int, default=3, help="runs per scenario (default 3)")
    args = parser.parse_args(argv)

    if args.live:
        try:
            make_client()
        except AgentError as exc:
            print(f"Cannot run live eval: {exc}")
            return 2

    print(f"Mode: {'LIVE model' if args.live else 'OFFLINE scripted model'}, {args.repeats} run(s) per scenario\n")
    total_pass = total_runs = 0
    for scenario in SCENARIOS:
        passes, counts, attempts = 0, [], []
        for _ in range(args.repeats):
            checks, n_tasks, n_attempts = run_once(scenario, args.live)
            ok = all(checks.values())
            passes += ok
            counts.append(n_tasks)
            attempts.append(n_attempts)
            if not ok:
                print(f"    failed checks: {[k for k, v in checks.items() if not v]}")
        total_pass, total_runs = total_pass + passes, total_runs + args.repeats
        consistent = "yes" if len(set(counts)) == 1 else f"no (task counts {sorted(set(counts))})"
        print(
            f"{scenario.name:<24} pass {passes}/{args.repeats}  "
            f"avg attempts {sum(attempts) / len(attempts):.1f}  same task count each run: {consistent}"
        )

    print(f"\nOverall: {total_pass}/{total_runs} runs passed all checks ({100 * total_pass / total_runs:.0f}%)")
    return 0 if total_pass == total_runs else 1


if __name__ == "__main__":
    sys.exit(main())
