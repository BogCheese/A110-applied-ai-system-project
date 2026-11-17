# AI Interactions Log

> **Stretch features only.** Only fill in the sections that apply to stretch features you attempted. If you did not attempt a stretch feature, leave its section blank or delete it. This file is not required for the core project.

---

## Agent Workflow (SF7)

This section documents the in-app planning agent (`PlannerAgent` in `pawpal_agent.py`): a plan, validate, act, check, revise loop. The saved traces below are produced by `python -m evals.demo` (offline, scripted model replies, so they are reproducible). The same trace appears in the app under "What the agent did", and every real run is also written to `pawpal.log`.

**What task did you give the agent?**

Turn a plain-English goal into real, scheduled care tasks for the owner's pets, without trusting the model's own judgment about whether the plan fits.

**What did the agent do?**

For each goal it asked the model for tasks as JSON, rejected malformed or unsafe items, added the valid ones to a dry-run copy of the owner, checked the copy with `Scheduler.detect_conflicts()` and `Scheduler.generate_plan()`, sent any problems back to the model (up to 3 revisions), and committed only at the end.

```text
=== Example 1: new puppy ===
Input : I just adopted a puppy, set up a daily care routine.
Pets  : Biscuit (dog); 120 minutes available
Agent trace:
  [plan] Attempt 1: model proposed 3 task(s)
  [check] Conflict: Morning feeding (Biscuit, 8:00 AM-8:10 AM) overlaps Walk (Biscuit, 8:00 AM-8:30 AM)
  [revise] Sending the problems back to the model to fix.
  [plan] Attempt 2: model proposed 3 task(s)
  [check] All checks passed: no new conflicts, everything fits.
  [commit] Added 3 task(s) to your pets; all checks passed.
Result: committed=True, attempts=2, issues left=0
Schedule:
  8:00 AM - 8:10 AM  Morning feeding (Biscuit)
  8:30 AM - 9:00 AM  Walk (Biscuit)
  6:00 PM - 6:10 PM  Evening feeding (Biscuit)

=== Example 2: two pets, tight day ===
Input : Care routine for my cat and dog, I only have 45 minutes a day.
Pets  : Mia (cat), Rex (dog); 45 minutes available
Agent trace:
  [plan] Attempt 1: model proposed 3 task(s)
  [check] All tasks together take 110 min but the owner only has 45 min (over by 65). Shorten tasks or drop the lowest-priority ones. As it stands these would be left out: 'Long hike' (Rex).
  [revise] Sending the problems back to the model to fix.
  [plan] Attempt 2: model proposed 3 task(s)
  [check] All checks passed: no new conflicts, everything fits.
  [commit] Added 3 task(s) to your pets; all checks passed.
Result: committed=True, attempts=2, issues left=0
Schedule:
  8:00 AM - 8:10 AM  Feeding (Mia)
  8:30 AM - 8:40 AM  Feeding (Rex)
  5:00 PM - 5:20 PM  Short walk (Rex)

=== Example 3: unknown pet guardrail ===
Input : Set up feeding for my dog Max.
Pets  : Max (dog); 60 minutes available
Agent trace:
  [plan] Attempt 1: model proposed 1 task(s)
  [validate] Rejected task 1 ('Feeding'): unknown pet 'Maxwell' (pets are: Max)
  [check] No valid tasks were produced.
  [revise] Sending the problems back to the model to fix.
  [plan] Attempt 2: model proposed 1 task(s)
  [check] All checks passed: no new conflicts, everything fits.
  [commit] Added 1 task(s) to your pets; all checks passed.
Result: committed=True, attempts=2, issues left=0
Schedule:
  8:00 AM - 8:10 AM  Feeding (Max)
```

**What did you have to verify or fix manually?**

The offline traces show the loop working, but they use scripted replies, so they do not show how a real model behaves. I verified the checks by reading them against the schedule, and by tests in `tests/test_agent.py` for each failure path (bad JSON, unknown pet, API error, over-budget, overlap). A live run against Gemini has not been logged here.

---

## Prompt Comparison (SF11)

> Compare two different prompts (or two different models) on the same task.

| | Option A | Option B |
|-|----------|----------|
| **Model / tool used** | | |
| **Prompt** | | |
| **Response summary** | | |
| **What was useful** | | |
| **Problems noticed** | | |
| **Decision** | | |

**Which approach did you use in your final implementation and why?**

<!-- Your conclusion -->
