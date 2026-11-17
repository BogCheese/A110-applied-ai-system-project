# PawPal+ presentation (about 6 minutes)

| Time | Slide or action | What to say |
|------|-----------------|-------------|
| 0:00-0:45 | **The problem and the base project** | PawPal+ started (Modules 1-3) as a pet-care scheduler: tasks with durations and priorities, a plan built within the owner's minutes, with a reason for every choice. This final version adds an AI planner on top. |
| 0:45-1:45 | **Architecture** (show `diagrams/architecture.mmd` rendered) | Input, then the agent loop, then output. The model only proposes tasks. Guardrails, a dry run on a copy, and the scheduler decide. Point out where the human sits (reviews the trace, edits, undoes) and where testing sits (pytest, eval). |
| 1:45-3:45 | **Live demo** (below) | Show the same flow the README logs show. |
| 3:45-4:45 | **Reliability** | 148 tests pass. The eval harness runs 4 scenarios x 3 runs and scores five checks: 12/12 offline. Be upfront: offline uses a scripted model, so it proves the loop, not the real model's accuracy. |
| 4:45-5:30 | **What I learned** | Most bugs were ordinary code at the edges (duplicate recurring tasks, a time box that would not clear, a ticked task jumping slots), found by tests and by running the app, not by reading code. A perfect score taught me to ask what it actually measures. |
| 5:30-6:00 | **Limits and next steps** | The checker only knows time, not whether a task is good care. Species coverage is thin. No saved data. Next: a recorded live eval, persistence, a "Plan my day" shortcut. |

## Demo script

1. **Terminal, no key needed:** `python -m evals.demo`. Walk through example 1: the agent proposes a walk that overlaps a feeding, the check flags it, the revision fixes it. Then example 3: the guardrail rejects the invented pet "Maxwell".
2. **App:** `streamlit run app.py`.
   - Day & pets: add a pet, set the day start and a busy time (work).
   - Plan with AI: type a goal and press Plan with AI; open "What the agent did".
   - Schedule: show the calendar and list. Tick a task: it is crossed out and stays in place. Open Edit to change it.
3. **Terminal:** `python -m evals.run_eval` and `python -m pytest -q` to show the reliability results.

## If something goes wrong

- No API key or the free tier is rate limited: use `python -m evals.demo` (offline); say it is the scripted model.
- The app will not start: `pip install -r requirements.txt` in the virtual environment.
