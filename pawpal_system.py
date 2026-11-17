"""Logic layer for the PawPal system.

This module holds the core classes described in diagrams/uml.mmd:

    Owner  --has-->  Pet  --has-->  Task
    Scheduler reads from an Owner and creates a DailyPlan.
    DailyPlan holds ScheduledItems and SkippedItems.

Times are stored as "minutes since midnight" (e.g. 8:30 AM = 510) so they can
be compared and added together without parsing strings. Use format_time() to
turn one back into text for display.
"""

from dataclasses import dataclass, field, fields, replace
from datetime import date, timedelta
from enum import Enum
from itertools import combinations
from uuid import uuid4


def format_time(minute: int) -> str:
    """Turn minutes since midnight into text, e.g. 510 -> '8:30 AM'."""
    hour, minutes = divmod(minute, 60)
    suffix = "AM" if hour % 24 < 12 else "PM"
    return f"{hour % 12 or 12}:{minutes:02d} {suffix}"


class Priority(Enum):
    """How important a task is. The value is used for sorting (higher = first)."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Frequency(Enum):
    """How often a task needs to be done."""

    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"


# How many days later a repeating task is due again. ONCE is missing on purpose.
RECURRENCE_DAYS = {Frequency.DAILY: 1, Frequency.WEEKLY: 7}


# eq=False: two tasks are only "the same" if they are the same object, so two
# identical-looking tasks can be told apart and a Task can be used in a set/dict.
@dataclass(eq=False)
class Task:
    """A single care activity for a pet (e.g. a walk or feeding)."""

    title: str
    duration_minutes: int
    priority: Priority
    frequency: Frequency = Frequency.ONCE
    completed: bool = False
    preferred_time: int | None = None  # minutes since midnight; None = no preference
    due_date: date = field(default_factory=date.today)  # the day this task needs doing
    pet_name: str = ""  # set by Pet.add_task so a plan can say which pet a task is for
    id: str = field(default_factory=lambda: uuid4().hex)
    completed_on: date | None = None  # the day it was marked done (set by mark_complete)
    spawned_task_id: str | None = None  # id of the next occurrence created when it was completed

    @property
    def preferred_end(self) -> int | None:
        """When the task would finish if started at its preferred time (None if it has none)."""
        if self.preferred_time is None:
            return None
        return self.preferred_time + self.duration_minutes

    def mark_complete(self) -> "Task | None":
        """Mark this task done and return the next occurrence if it repeats (else None).

        A DAILY task's next occurrence is due 1 day later and a WEEKLY task's 7 days later,
        counted from the later of today and this task's own due date. Counting from today
        means a task finished late is never handed a due date in the past; counting from the
        due date means finishing a task that isn't due yet can't create a copy due the same
        day as itself. The returned task is a copy that is not completed and has a new id.
        It is not stored anywhere; use Pet.complete_task() to add it to the pet.
        """
        self.completed = True
        self.completed_on = date.today()

        days_until_next = RECURRENCE_DAYS.get(self.frequency)
        if days_until_next is None:
            return None

        # Copy this task for its next due date: not completed, with a fresh id.
        start_from = max(date.today(), self.due_date)
        return replace(
            self,
            completed=False,
            completed_on=None,
            spawned_task_id=None,
            due_date=start_from + timedelta(days=days_until_next),
            id=uuid4().hex,
        )

    def priority_value(self) -> int:
        """Return the priority as a number so tasks can be sorted."""
        return self.priority.value


# eq=False for the same reason as Task: pets are compared by identity, so two
# pets with the same name and species are still different pets.
@dataclass(eq=False)
class Pet:
    """A pet and the care tasks that belong to it."""

    name: str
    species: str
    tasks: list[Task] = field(default_factory=list)

    def add_task(self, task: Task) -> None:
        """Add a care task to this pet and record the pet's name on the task."""
        task.pet_name = self.name
        self.tasks.append(task)

    def edit_task(self, task: Task, **changes) -> None:
        """Update fields on an existing task, e.g. edit_task(t, priority=Priority.HIGH).

        Raises ValueError if the task is not this pet's, or if a key in
        `changes` is not an editable field of Task.
        """
        if task not in self.tasks:
            raise ValueError(f"{task.title!r} is not one of {self.name}'s tasks")

        # These are managed by the system, so they can't be edited.
        editable = {f.name for f in fields(Task)} - {"id", "pet_name", "completed_on", "spawned_task_id"}
        unknown = set(changes) - editable
        if unknown:
            raise ValueError(f"Cannot edit unknown task field(s): {sorted(unknown)}")

        for field_name, new_value in changes.items():
            setattr(task, field_name, new_value)

    def remove_task(self, task: Task) -> None:
        """Remove a care task from this pet (raises ValueError if it isn't here)."""
        self.tasks.remove(task)

    def complete_task(self, task: Task) -> Task | None:
        """Mark a task done and, if it repeats, add its next occurrence to this pet.

        Returns the new task, or None for a one-time task or a task that was already
        completed (so a double click can't create duplicates). Raises ValueError if
        the task does not belong to this pet, or is not due yet (finishing tomorrow's
        copy of a daily task would otherwise create yet another copy, forever).
        """
        if task not in self.tasks:
            raise ValueError(f"{task.title!r} is not one of {self.name}'s tasks")
        if task.completed:
            return None
        if task.due_date > date.today():
            raise ValueError(f"{task.title!r} isn't due until {task.due_date:%b %d}, so it can't be marked done yet")

        next_task = task.mark_complete()
        if next_task is not None:
            self.add_task(next_task)
            task.spawned_task_id = next_task.id  # remembered so an undo can take it back
        return next_task

    def reopen_task(self, task: Task) -> None:
        """Undo complete_task: mark the task not done again.

        If completing it created a next occurrence that hasn't been done yet, that copy is
        removed, so undoing leaves the list as it was. Does nothing for a task that isn't
        done. Raises ValueError if the task does not belong to this pet.
        """
        if task not in self.tasks:
            raise ValueError(f"{task.title!r} is not one of {self.name}'s tasks")
        if not task.completed:
            return

        task.completed = False
        task.completed_on = None
        if task.spawned_task_id is not None:
            spawned = next((t for t in self.tasks if t.id == task.spawned_task_id), None)
            if spawned is not None and not spawned.completed:
                self.tasks.remove(spawned)
            task.spawned_task_id = None


@dataclass
class Preferences:
    """Scheduling preferences an owner can set."""

    buffer_minutes: int = 0  # gap to leave between consecutive tasks


@dataclass
class Owner:
    """A pet owner with limited time and scheduling preferences."""

    name: str
    available_minutes: int
    day_start_minute: int = 8 * 60  # when the schedule begins; default 8:00 AM
    preferences: Preferences = field(default_factory=Preferences)
    pets: list[Pet] = field(default_factory=list)
    day_end_minute: int | None = None  # optional: nothing may run past this time
    # Optional blocks the owner is unavailable (e.g. work 9-5), as (start, end) minutes.
    busy_blocks: list[tuple[int, int]] = field(default_factory=list)

    def add_busy_block(self, start_minute: int, end_minute: int) -> None:
        """Mark a time range as unavailable. Raises ValueError if the range is empty or backwards."""
        if not 0 <= start_minute < end_minute <= 24 * 60:
            raise ValueError("A busy block must start before it ends, within one day")
        self.busy_blocks.append((start_minute, end_minute))
        self.busy_blocks.sort()

    def remove_busy_block(self, block: tuple[int, int]) -> None:
        """Remove a busy block (raises ValueError if it isn't here)."""
        self.busy_blocks.remove(block)

    def add_pet(self, pet: Pet) -> None:
        """Add a pet to this owner."""
        self.pets.append(pet)

    def remove_pet(self, pet: Pet) -> None:
        """Remove a pet from this owner (raises ValueError if it isn't here)."""
        self.pets.remove(pet)

    def get_all_tasks(self) -> list[Task]:
        """Return every task across all of this owner's pets, in pet order."""
        return [task for pet in self.pets for task in pet.tasks]

    def filter_tasks(self, pet_name: str | None = None, completed: bool | None = None) -> list[Task]:
        """Return tasks matching the given pet name and/or completed status.

        Pass None for a filter to ignore it. With both given, a task must match both.
        Example: filter_tasks(pet_name="Mia", completed=False) is Mia's unfinished tasks.
        """
        matching = self.get_all_tasks()
        if pet_name is not None:
            matching = [task for task in matching if task.pet_name == pet_name]
        if completed is not None:
            matching = [task for task in matching if task.completed == completed]
        return matching


@dataclass
class ScheduledItem:
    """A task placed on the plan, with when it starts and why it was chosen."""

    task: Task
    start_minute: int  # minutes since midnight
    reason: str

    @property
    def end_minute(self) -> int:
        """When the task finishes."""
        return self.start_minute + self.task.duration_minutes

    @property
    def done(self) -> bool:
        """True if the task was already finished (it stays on the plan, marked done)."""
        return self.task.completed


@dataclass
class SkippedItem:
    """A task that did not make it onto the plan, with the reason."""

    task: Task
    reason: str


@dataclass
class DailyPlan:
    """The result of scheduling: what was planned and what was skipped, and why."""

    scheduled_items: list[ScheduledItem] = field(default_factory=list)
    skipped_items: list[SkippedItem] = field(default_factory=list)

    @property
    def total_minutes(self) -> int:
        """Total scheduled time. Computed so it can never drift out of sync."""
        return sum(item.task.duration_minutes for item in self.scheduled_items)

    def add_item(self, task: Task, start_minute: int, reason: str) -> None:
        """Schedule a task to start at the given time (items stay sorted by start)."""
        self.scheduled_items.append(ScheduledItem(task, start_minute, reason))
        self.scheduled_items.sort(key=lambda item: item.start_minute)

    def skip(self, task: Task, reason: str) -> None:
        """Record that a task could not be scheduled, and why."""
        self.skipped_items.append(SkippedItem(task, reason))

    def display(self) -> str:
        """Return the plan as human-readable text: the timeline, then what was skipped."""
        lines = ["Today's Schedule", "----------------"]

        if not self.scheduled_items:
            lines.append("Nothing scheduled.")
        for item in self.scheduled_items:
            lines.append(
                f"{format_time(item.start_minute)} - {format_time(item.end_minute)}  "
                f"{item.task.title} ({item.task.pet_name})" + (" [done]" if item.done else "")
            )
        lines.append(f"Total: {self.total_minutes} minutes")

        if self.skipped_items:
            lines.append("")
            lines.append("Skipped:")
            for skipped in self.skipped_items:
                lines.append(f"- {skipped.task.title} ({skipped.task.pet_name})")

        return "\n".join(lines)


class Scheduler:
    """Builds a DailyPlan from an owner's pets, tasks, and available time."""

    def __init__(self, owner: Owner) -> None:
        """Create a scheduler that plans for the given owner and all of their pets."""
        self.owner = owner

    @staticmethod
    def sort_by_time(tasks: list[Task]) -> list[Task]:
        """Return a new list of tasks ordered by preferred time, earliest first.

        Tasks with no preferred time go at the end. Tasks with the same time keep
        their original relative order. The list passed in is not changed.
        """
        return sorted(tasks, key=lambda task: (task.preferred_time is None, task.preferred_time or 0))

    def detect_conflicts(self, plan_date: date | None = None) -> list[str]:
        """Return a warning for every pair of pending tasks whose preferred times overlap.

        This only reports problems, it never changes anything. The scheduler later
        resolves a conflict by moving one task to the next free slot.
        """
        plan_date = plan_date or date.today()
        timed_tasks = [
            task
            for task in self.owner.get_all_tasks()
            if task.preferred_time is not None and self._is_pending(task, plan_date)
        ]

        # Compare every pair once. Simple and fine for the handful of tasks one owner has.
        return [
            self._describe_conflict(first, second)
            for first, second in combinations(self.sort_by_time(timed_tasks), 2)
            if self._overlaps(first, second)
        ]

    @staticmethod
    def _overlaps(first: Task, second: Task) -> bool:
        """True if two tasks' preferred time ranges overlap (touching end to start is fine)."""
        return first.preferred_time < second.preferred_end and second.preferred_time < first.preferred_end

    @staticmethod
    def _describe_conflict(first: Task, second: Task) -> str:
        """Build the warning message for two conflicting tasks."""

        def describe(task: Task) -> str:
            times = f"{format_time(task.preferred_time)}-{format_time(task.preferred_end)}"
            return f"{task.title} ({task.pet_name}, {times})"

        return f"Conflict: {describe(first)} overlaps {describe(second)}"

    def generate_plan(self, plan_date: date | None = None) -> DailyPlan:
        """Choose and order tasks that fit within the owner's available time.

        Rules:
        - Tasks come from owner.get_all_tasks(), so every pet is included.
        - Unfinished tasks due on or before plan_date (default: today) are considered.
        - Tasks finished on plan_date stay on the plan, marked done, in the slot they would
          have had, so completing a task never makes it vanish from the day. They are placed
          first and their minutes still count (that time has been spent).
        - Greedy: take tasks by priority (high first), then shorter duration,
          then pet order, so the result is repeatable. Tradeoff: one long
          high-priority task can use up time that several smaller ones would fit.
        - A task is scheduled at its preferred_time if that slot is free;
          otherwise it goes in the earliest free slot after owner.day_start_minute.
        - Nothing is placed past owner.day_end_minute (if set) or inside a busy block;
          a task with no such slot is skipped with a reason.
        - Only task minutes count against owner.available_minutes; the buffer between
          tasks is enforced as spacing but is not charged to that budget.
        - Every task ends up either scheduled or skipped, each with a reason.
        """
        plan_date = plan_date or date.today()
        plan = DailyPlan()
        buffer = self.owner.preferences.buffer_minutes
        minutes_left = self.owner.available_minutes

        # Step 1: unfinished tasks that are due, plus tasks finished today (kept, shown as done).
        to_schedule = self.tasks_in_plan(plan_date)

        # Step 2: finished tasks first (they already happened), then highest priority first and
        # shorter tasks first within a priority. sort() is stable, so ties keep the pet order.
        to_schedule.sort(key=lambda task: (not task.completed, -task.priority_value(), task.duration_minutes))

        # Step 3: place each task if it fits in the time that is left.
        for task in to_schedule:
            if task.completed:
                # Already done: always keep it on the plan; just record the time it used.
                chosen = self._choose_start(plan, task, buffer)
                if chosen is not None:
                    start, placement = chosen
                    plan.add_item(task, start, f"done, {placement}")
                    minutes_left = max(0, minutes_left - task.duration_minutes)
                continue

            # Only the task's own minutes count against the owner's available time. The
            # gap between tasks is spacing, not time spent on care, so it is not charged.
            if task.duration_minutes > minutes_left:
                plan.skip(
                    task,
                    f"needs {task.duration_minutes} min but only {minutes_left} min left "
                    f"of your {self.owner.available_minutes} minutes",
                )
                continue

            chosen = self._choose_start(plan, task, buffer)
            if chosen is None:
                plan.skip(task, "no free time left in your day (check your day end and busy times)")
                continue
            start, placement = chosen
            reason = f"{task.priority.name.lower()} priority, {placement}"
            plan.add_item(task, start, reason)
            minutes_left -= task.duration_minutes

        return plan

    def explain_plan(self, plan: DailyPlan) -> str:
        """Return a plain-language explanation built from the reasons in the plan."""
        lines = ["Why this plan:"]
        for item in plan.scheduled_items:
            lines.append(f"- {item.task.title}: {item.reason}")
        for skipped in plan.skipped_items:
            lines.append(f"- {skipped.task.title} (skipped): {skipped.reason}")
        return "\n".join(lines)

    def _is_pending(self, task: Task, plan_date: date) -> bool:
        """True if the task is due on or before plan_date and not yet completed."""
        return task.due_date <= plan_date and not task.completed

    @staticmethod
    def finished_on(task: Task, plan_date: date) -> bool:
        """True if the task was marked done on plan_date (older tasks without a record use their due date)."""
        return task.completed and (task.completed_on or task.due_date) == plan_date

    def tasks_in_plan(self, plan_date: date | None = None) -> list[Task]:
        """The tasks a plan for plan_date covers: unfinished ones due by then, plus ones finished that day."""
        plan_date = plan_date or date.today()
        return [
            task
            for task in self.owner.get_all_tasks()
            if self._is_pending(task, plan_date) or self.finished_on(task, plan_date)
        ]

    def _choose_start(self, plan: DailyPlan, task: Task, buffer: int) -> tuple[int, str] | None:
        """Pick a start time for the task and describe how it was chosen (None if nothing fits)."""
        preferred = task.preferred_time
        if preferred is not None and self._is_free(plan, preferred, task, buffer):
            return preferred, f"placed at its preferred time {format_time(preferred)}"

        start = self._earliest_free_start(plan, task, buffer)
        if start is None:
            return None
        if preferred is not None:
            return start, "preferred time was taken or unavailable, placed in the next free slot"
        return start, "placed in the earliest free slot"

    def _is_free(self, plan: DailyPlan, start: int, task: Task, buffer: int) -> bool:
        """True if the task fits at `start`: inside the day, outside busy blocks, and clear of other tasks."""
        end = start + task.duration_minutes
        day_end = self.owner.day_end_minute
        if day_end is not None and end > day_end:
            return False
        for block_start, block_end in self.owner.busy_blocks:
            if start < block_end and block_start < end:
                return False
        for item in plan.scheduled_items:
            ends_before_item = end + buffer <= item.start_minute
            starts_after_item = start >= item.end_minute + buffer
            if not (ends_before_item or starts_after_item):
                return False
        return True

    def _earliest_free_start(self, plan: DailyPlan, task: Task, buffer: int) -> int | None:
        """Find the first start time, from the day start onward, where the task fits.

        The earliest possible start is either the day start or the moment something
        in the way (a scheduled task or a busy block) ends, so only those are tried.
        Returns None if the task fits nowhere in the day.
        """
        day_start = self.owner.day_start_minute
        candidates = {day_start}
        candidates.update(item.end_minute + buffer for item in plan.scheduled_items)
        candidates.update(block_end for _, block_end in self.owner.busy_blocks)
        for start in sorted(candidates):
            if start >= day_start and self._is_free(plan, start, task, buffer):
                return start
        return None
