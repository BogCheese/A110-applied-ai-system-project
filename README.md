# PawPal+ (Module 2 Project)

You are building **PawPal+**, a Streamlit app that helps a pet owner plan care tasks for their pet.

## Scenario

A busy pet owner needs help staying consistent with pet care. They want an assistant that can:

- Track pet care tasks (walks, feeding, meds, enrichment, grooming, etc.)
- Consider constraints (time available, priority, owner preferences)
- Produce a daily plan and explain why it chose that plan

Your job is to design the system first (UML), then implement the logic in Python, then connect it to the Streamlit UI.

## What you will build

Your final app should:

- Let a user enter basic owner + pet info
- Let a user add/edit tasks (duration + priority at minimum)
- Generate a daily schedule/plan based on constraints and priorities
- Display the plan clearly (and ideally explain the reasoning)
- Include tests for the most important scheduling behaviors

## Features

- **Multiple pets, one schedule.** An owner can have any number of pets, and the scheduler reads every pet's tasks together through `Owner.get_all_tasks()`.
- **Priority-based daily plan.** Tasks are chosen greedily: high priority first, then shorter tasks, then pet order, until the owner's available minutes run out. Tasks that don't fit are skipped with a reason.
- **Preferred times with a fallback.** A task is placed at its preferred time if that slot is free. Otherwise it goes in the earliest free slot after the day's start time.
- **Sorting by time.** Tasks can be listed in time order, with tasks that have no preferred time last (`Scheduler.sort_by_time()`).
- **Filtering.** Tasks can be filtered by pet, by completion status, or both (`Owner.filter_tasks()`).
- **Conflict warnings.** Tasks whose preferred times overlap, for the same pet or different pets, produce a readable warning instead of an error (`Scheduler.detect_conflicts()`).
- **Daily and weekly recurrence.** Completing a repeating task creates the next one, due tomorrow (daily) or in 7 days (weekly). Completing the same task twice does not create duplicates.
- **Explained plans.** Every scheduled and skipped task comes with a plain-language reason (`Scheduler.explain_plan()`).
- **Your day, your rules.** Set when your day starts, optionally when it ends, and add busy times such as work 9 AM - 5 PM. Nothing is scheduled in a busy time or past the day end, and the AI planner avoids them too.
- **Edit and delete tasks** in the UI, with due dates shown in the task table.
- **Two-panel layout.** On a wide screen you set things up on the left (your day, your pets, the AI planner) and watch the results on the right (today's schedule, then your tasks grouped by pet), so you can see the schedule change as you fill things in. Pets appear as cards you can delete (after a confirmation), and **Start over** clears everything.
- **The schedule builds itself.** There is no Generate button: the schedule on the right is rebuilt on every change. It is a timeline that includes busy times and free time.
- **Themed look.** Colours, fonts and rounded corners come from `.streamlit/config.toml` (a teal accent with matching light and dark modes), not from CSS in the code.
- **Calendar view.** The schedule is drawn like a calendar: time runs top to bottom, each pet has its own colour, and every block shows its title, time and priority. Long busy stretches such as work are squeezed into a grey band so the rest stays readable, and the same schedule is available as a plain list. The drawing lives in `calendar_view.py` and is tested without a browser.
- **Time budget.** "Time I can spend on pet care" counts task minutes only. The 5-minute gap between tasks is kept as spacing but is not charged to that budget. The AI is told how many minutes are already used, and when a plan is over budget it is told the total, by how much, and which tasks would be left out.
- **Accessibility.** Every input has a unique label, delete buttons name what they delete, times use standard 12-hour format, and important text is never in low-contrast caption style. Reviewed against WCAG 2.1 AA; a screen reader pass is still worth doing by hand.
- **Tested.** 103 automated tests cover scheduling, busy times, sorting, filtering, recurrence, conflicts, the AI agent, and the Streamlit page itself.

## Getting started

### Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Suggested workflow

1. Read the scenario carefully and identify requirements and edge cases.
2. Draft a UML diagram (classes, attributes, methods, relationships).
3. Convert UML into Python class stubs (no logic yet).
4. Implement scheduling logic in small increments.
5. Add tests to verify key behaviors.
6. Connect your logic to the Streamlit UI in `app.py`.
7. Refine UML so it matches what you actually built.

## AI Planning Agent

Type a goal like *"I just adopted a puppy, set up a daily care routine"* and the agent adds real tasks to your pets. It is an **agentic workflow** (plan, act, check, revise) and the checks are PawPal's own scheduler, not the model's opinion. See [diagrams/architecture.mmd](diagrams/architecture.mmd).

1. **Plan**: The model (Gemini or Claude) proposes tasks as JSON.
2. **Validate (guardrails)**: unknown pets, bad durations, priorities, or times are rejected and reported back to the model. A task that duplicates an existing one (same pet, title and time) is skipped; a second feeding at a different time is fine.
3. **Act on a copy**: tasks are added to a dry-run copy of your data.
4. **Check**: `Scheduler.detect_conflicts()` and `Scheduler.generate_plan()` find new conflicts and tasks that don't fit your minutes, and times that fall in a busy block or outside your day.
5. **Revise**: problems go back to the model, up to 3 times. Only then are tasks added to your real pets. If problems remain, they are shown to you instead of hidden.

Gemini calls retry automatically when the service is busy or rate limited. If the API is down or the model never returns a usable plan, you get an error message and your data is unchanged. Everything is logged to `pawpal.log`.

### AI setup

PawPal+ works with Google Gemini (free tier) or Anthropic Claude. Get a free Gemini key at https://aistudio.google.com/apikey, then:

Easiest: copy `.env.example` to a file named `.env` in the project folder (it is git-ignored, so your key is never committed), and put your key in it:

```
GEMINI_API_KEY=your-key-here
```

Then run `streamlit run app.py`. The app loads `.env` automatically. Setting `GEMINI_API_KEY` in your shell also works and takes priority over the file.

Using Claude instead: `pip install anthropic` and set `ANTHROPIC_API_KEY`. If both keys are set, choose with `PAWPAL_PROVIDER=gemini|anthropic`. Override the model with `PAWPAL_MODEL` (defaults: `gemini-3.5-flash`, `claude-haiku-5-5`). The rest of the app works without a key.

### Reliability checks

```bash
python -m pytest                      # 103 tests; the AI and the Streamlit page are tested with a scripted fake model, no network or key
python -m evals.run_eval              # offline: 4 scenarios x 3 runs with a scripted model
python -m evals.run_eval --live --repeats 5   # real model (free Gemini tier is rate limited: use fewer repeats if you hit it): pass rate and consistency
```

The eval scores each run on: plan committed, no conflicts, fits available time, every pet covered, every dog/cat has a feeding task. It also reports average attempts and whether the task count was the same across repeats. The offline mode checks the harness and the revision loop; only `--live` measures the real model.

## Implementation Summary

The logic lives in `pawpal_system.py` and can be tried from the terminal with `python main.py`.

- **`Task`**: one care activity (title, duration, priority, frequency, optional preferred time, completion status).
- **`Pet`**: a pet and its list of tasks. It adds, edits, and removes its own tasks.
- **`Owner`**: has the available minutes for the day and a list of pets. `get_all_tasks()` returns every pet's tasks together.
- **`Scheduler`**: the "brain". `generate_plan()` reads tasks from `Owner.get_all_tasks()` (so all pets are covered), skips completed tasks, orders the rest by priority then duration, and places each one at its preferred time if free or in the earliest free slot until the owner's time runs out.
- **`DailyPlan`**: the result. It holds the scheduled items (with start times and reasons) and the skipped items (with reasons), and `display()` prints it.

How they work together: `Owner` → `Pet` → `Task` hold the data, `Scheduler` reads from the `Owner` and creates a `DailyPlan`. See [diagrams/uml.mmd](diagrams/uml.mmd) for the full class diagram.

## 🖥️ Sample Output

This is the generated plan from `python main.py` for an owner with two pets (Buddy the dog and Mia the cat) and 120 minutes available. The full demo output, including sorting, conflict warnings, and filtering, is in the Demo Walkthrough below.

```
Today's Schedule
----------------
7:00 AM - 7:10 AM  Flea treatment (Buddy)
7:15 AM - 7:25 AM  Feed breakfast (Mia)
8:00 AM - 8:05 AM  Give medication (Mia)
12:00 PM - 12:20 PM  Play with laser (Mia)
6:00 PM - 6:15 PM  Brush coat (Buddy)
Total: 60 minutes

Skipped:
- Morning walk (Buddy)

Why this plan:
- Flea treatment: medium priority, placed at its preferred time 7:00 AM
- Feed breakfast: high priority, placed at its preferred time 7:15 AM
- Give medication: high priority, placed at its preferred time 8:00 AM
- Play with laser: medium priority, placed at its preferred time 12:00 PM
- Brush coat: low priority, placed at its preferred time 6:00 PM
- Morning walk (skipped): already completed
```

## 🧪 Testing PawPal+

Run the tests with:

```bash
python -m pytest
```

The 26 tests in `tests/test_pawpal.py` cover:

- **Basics:** marking a task complete, adding a task to a pet, and editing a task (unknown fields and `id` are rejected).
- **Sorting:** tasks come back in time order with untimed tasks last, equal times keep their order, and an empty list or the original list is left unchanged.
- **Filtering:** by pet, by completion status, and by both, plus an unknown pet returning nothing.
- **Recurring tasks:** a daily task creates a copy due tomorrow, a weekly task one due in 7 days, a one-time task creates nothing, a late completion is still due tomorrow, and completing the same task twice doesn't duplicate it.
- **Conflict detection:** same-time and overlapping tasks (same pet and different pets) are flagged, back-to-back, completed, and untimed tasks are not, and three tasks at one time give three warnings.
- **Scheduling:** priority order, skipping tasks that don't fit or are already completed, ignoring tasks not due yet, moving a clashing task to the next free slot, and empty cases (no pets, a pet with no tasks).

Output of a successful run:

```
============================= test session starts =============================
platform win32 -- Python 3.13.15, pytest-9.1.1, pluggy-1.7.0
rootdir: C:\Users\Brand\Desktop\Codepath A110\ai110-module2show-pawpal-starter
configfile: pytest.ini
plugins: anyio-4.15.1
collected 26 items

tests\test_pawpal.py ..........................                          [100%]

============================= 26 passed in 0.08s ==============================
```

**Confidence Level: ★★★★☆ (4 out of 5)**

All 26 tests pass, and they cover the main behaviors and the edge cases I could think of, including one real bug found and fixed (completing a recurring task twice). I'm not giving 5 stars because month and year rollover for recurring dates isn't tested, the Streamlit UI has no automated tests, tasks that cross midnight display without a "next day" marker, and the greedy scheduler can pick a worse plan than the best possible one.

## 📐 Smarter Scheduling

| Feature | Method(s) | Notes |
|---------|-----------|-------|
| Task sorting | `Scheduler.sort_by_time()` | Orders tasks by preferred time; tasks with no time go last. |
| Filtering | `Owner.filter_tasks()` | Filters by pet name and/or completion status. |
| Conflict handling | `Scheduler.detect_conflicts()` | Warns when two tasks' preferred times overlap. |
| Recurring tasks | `Task.mark_complete()`, `Pet.complete_task()` | Completing a daily or weekly task creates the next one. |

### Sorting

`Scheduler.sort_by_time(tasks)` returns a new list ordered by `preferred_time` (stored as minutes since midnight), earliest first. Tasks without a preferred time go at the end, and the original list is not changed. Separately, `Scheduler.generate_plan()` orders tasks by priority (high first), then shorter duration, then pet order, and the finished `DailyPlan` is always listed by start time.

### Filtering

`Owner.filter_tasks(pet_name=None, completed=None)` returns the tasks that match. Leave a filter as `None` to ignore it, or give both to require both. For example, `owner.filter_tasks(pet_name="Mia", completed=False)` returns Mia's unfinished tasks.

### Conflict detection

`Scheduler.detect_conflicts()` looks at every pair of pending tasks (due today or earlier, and not completed) that have a preferred time, across all pets. Two tasks conflict when each starts before the other ends, so identical times and partial overlaps are both caught, while back-to-back tasks are fine. It returns a list of warning messages (empty if there are none) and never raises an error or changes any task. When `generate_plan()` later builds the schedule, it resolves a clash by keeping the higher-priority task at its time and moving the other to the earliest free slot.

### Recurring tasks

Each `Task` has a `frequency` (`ONCE`, `DAILY`, or `WEEKLY`) and a `due_date`. `Task.mark_complete()` marks the task done and, for a repeating task, returns a copy that is not completed and is due today + 1 day (daily) or today + 7 days (weekly), calculated with `timedelta`. `Pet.complete_task(task)` calls it and adds the new task to the pet. `generate_plan()` only includes tasks due on or before the plan date, so tomorrow's copy stays out of today's schedule.

## 📸 Demo Walkthrough

Start the app with `streamlit run app.py`. You can also try the logic without the UI by running `python main.py`.

### What you can do in the app

- **Owner:** set your name and how many minutes you have available today.
- **Pets:** add a pet with a name and species. A table lists your pets and how many tasks each has.
- **Tasks:** pick a pet, then give the task a title, duration, priority, frequency (once, daily, or weekly), and an optional preferred time.
- **Your tasks:** see all tasks in a table sorted by time, and filter it by pet and by status (all, to do, done).
- **Conflict warnings:** if two tasks' preferred times overlap, an orange warning names both tasks and tells you how to fix it. A green message appears when there are no conflicts.
- **Mark a task complete:** finish a task, and if it repeats the app tells you when the next one is due.
- **Build Schedule:** generate today's plan, with a summary, a table of start and end times with the reason for each, and a list of tasks that were left out.

### Example workflow

1. Set the owner to "Jordan" with 120 minutes available.
2. Add two pets: Buddy (dog) and Mia (cat).
3. Add a "Walk" for Buddy (30 min, high priority, daily, 9:00 AM) and "Meds" for Mia (10 min, high priority, once, 9:00 AM).
4. Look at the task table, which is sorted by time. A conflict warning appears because both tasks want 9:00 AM.
5. Look at today's schedule (it updates by itself). Meds (shorter, so it goes first) keeps 9:00 AM, and Walk moves to the earliest free slot at 8:00 AM. Hover a block on the calendar to see why it was placed there.
6. Select "Walk" under **Mark a task complete**. Because it is daily, the app creates tomorrow's Walk and the conflict goes away for today.

### Scheduler behaviors shown

- **Sorting:** tasks added out of order are listed by time of day.
- **Conflict warnings:** overlapping tasks are flagged for the same pet and across pets, without crashing.
- **Priority and time budget:** higher-priority tasks are scheduled first, and tasks that don't fit are skipped with a reason.
- **Preferred time fallback:** a task that loses its slot is moved to the next free one.
- **Recurrence:** completing a daily task adds a copy due tomorrow, which stays out of today's plan.
- **Filtering:** tasks can be narrowed to one pet or to done or unfinished tasks.

### Sample CLI output

Output from `python main.py`. It adds tasks out of order, shows sorting and a conflict check, completes a daily task, filters tasks, and prints the final schedule:

```
Owner: Jordan (120 minutes available)
Pets: Buddy, Mia

Tasks sorted by time:
    7:00 AM  Morning walk (Buddy) [to do, due 2026-10-25]
    7:00 AM  Flea treatment (Buddy) [to do, due 2026-10-25]
    7:15 AM  Feed breakfast (Mia) [to do, due 2026-10-25]
    8:00 AM  Give medication (Mia) [to do, due 2026-10-25]
   12:00 PM  Play with laser (Mia) [to do, due 2026-10-25]
    6:00 PM  Brush coat (Buddy) [to do, due 2026-10-25]

Conflict check:
  Conflict: Morning walk (Buddy, 7:00 AM-7:30 AM) overlaps Flea treatment (Buddy, 7:00 AM-7:10 AM)
  Conflict: Morning walk (Buddy, 7:00 AM-7:30 AM) overlaps Feed breakfast (Mia, 7:15 AM-7:25 AM)

Completed 'Morning walk'; next one is due 2026-10-26.

Mia's tasks:
   12:00 PM  Play with laser (Mia) [to do, due 2026-10-25]
    8:00 AM  Give medication (Mia) [to do, due 2026-10-25]
    7:15 AM  Feed breakfast (Mia) [to do, due 2026-10-25]

Completed tasks:
    7:00 AM  Morning walk (Buddy) [done, due 2026-10-25]

Today's Schedule
----------------
7:00 AM - 7:10 AM  Flea treatment (Buddy)
7:15 AM - 7:25 AM  Feed breakfast (Mia)
8:00 AM - 8:05 AM  Give medication (Mia)
12:00 PM - 12:20 PM  Play with laser (Mia)
6:00 PM - 6:15 PM  Brush coat (Buddy)
Total: 60 minutes

Skipped:
- Morning walk (Buddy)

Why this plan:
- Flea treatment: medium priority, placed at its preferred time 7:00 AM
- Feed breakfast: high priority, placed at its preferred time 7:15 AM
- Give medication: high priority, placed at its preferred time 8:00 AM
- Play with laser: medium priority, placed at its preferred time 12:00 PM
- Brush coat: low priority, placed at its preferred time 6:00 PM
- Morning walk (skipped): already completed
```

In this run the morning walk was completed before the schedule was built, so it is skipped as "already completed", and tomorrow's copy is not part of today's plan.

**Screenshot or video** *(optional)*: <!-- Insert a screenshot or link to a demo video here -->
