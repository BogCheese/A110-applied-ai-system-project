from datetime import date, timedelta

import pytest

from pawpal_system import Frequency, Owner, Pet, Priority, Scheduler, Task


def make_owner() -> tuple[Owner, Pet, Pet]:
    """An owner with two pets, used by the tests below."""
    owner = Owner("Jordan", available_minutes=120)
    buddy, mia = Pet("Buddy", "dog"), Pet("Mia", "cat")
    owner.add_pet(buddy)
    owner.add_pet(mia)
    return owner, buddy, mia


# --- Basics ---------------------------------------------------------------


def test_mark_complete_changes_task_status():
    task = Task("Morning walk", 30, Priority.HIGH)

    task.mark_complete()

    assert task.completed is True


def test_add_task_increases_pet_task_count():
    pet = Pet("Buddy", "dog")

    pet.add_task(Task("Morning walk", 30, Priority.HIGH))

    assert len(pet.tasks) == 1


# --- Sorting --------------------------------------------------------------


def test_sort_by_time_orders_by_preferred_time_with_unscheduled_last():
    late = Task("Evening brush", 10, Priority.LOW, preferred_time=18 * 60)
    anytime = Task("Cuddle", 10, Priority.LOW)
    early = Task("Morning walk", 30, Priority.HIGH, preferred_time=7 * 60)

    sorted_tasks = Scheduler.sort_by_time([late, anytime, early])

    assert sorted_tasks == [early, late, anytime]


# --- Filtering ------------------------------------------------------------


def test_filter_tasks_by_pet_and_status():
    owner, buddy, mia = make_owner()
    walk = Task("Walk", 30, Priority.HIGH)
    brush = Task("Brush", 10, Priority.LOW)
    meds = Task("Meds", 5, Priority.HIGH)
    buddy.add_task(walk)
    buddy.add_task(brush)
    mia.add_task(meds)
    brush.mark_complete()

    assert owner.filter_tasks(pet_name="Buddy") == [walk, brush]
    assert owner.filter_tasks(completed=False) == [walk, meds]
    assert owner.filter_tasks(pet_name="Buddy", completed=True) == [brush]
    assert owner.filter_tasks() == [walk, brush, meds]


# --- Recurring tasks ------------------------------------------------------


def test_completing_daily_task_creates_task_due_tomorrow():
    pet = Pet("Buddy", "dog")
    today = date.today()
    walk = Task("Walk", 30, Priority.HIGH, Frequency.DAILY, due_date=today)
    pet.add_task(walk)

    next_walk = pet.complete_task(walk)

    assert walk.completed is True
    assert next_walk.due_date == today + timedelta(days=1)
    assert next_walk.completed is False
    assert next_walk in pet.tasks
    assert next_walk.pet_name == "Buddy"
    assert next_walk.id != walk.id


def test_completing_overdue_daily_task_is_due_tomorrow_not_relative_to_old_date():
    pet = Pet("Buddy", "dog")
    three_days_ago = date.today() - timedelta(days=3)
    walk = Task("Walk", 30, Priority.HIGH, Frequency.DAILY, due_date=three_days_ago)
    pet.add_task(walk)

    next_walk = pet.complete_task(walk)

    assert next_walk.due_date == date.today() + timedelta(days=1)


def test_completing_weekly_task_creates_task_due_in_seven_days():
    pet = Pet("Buddy", "dog")
    today = date.today()
    brush = Task("Brush", 15, Priority.LOW, Frequency.WEEKLY, due_date=today)
    pet.add_task(brush)

    next_brush = pet.complete_task(brush)

    assert next_brush.due_date == today + timedelta(days=7)


def test_completing_one_time_task_does_not_repeat():
    pet = Pet("Buddy", "dog")
    vet = Task("Vet visit", 60, Priority.HIGH, Frequency.ONCE)
    pet.add_task(vet)

    assert pet.complete_task(vet) is None
    assert len(pet.tasks) == 1


def test_complete_task_rejects_task_from_another_pet():
    pet = Pet("Buddy", "dog")

    with pytest.raises(ValueError):
        pet.complete_task(Task("Walk", 30, Priority.HIGH))


def test_plan_ignores_tasks_not_due_yet():
    owner, buddy, _ = make_owner()
    today = date.today()
    due_today = Task("Walk", 30, Priority.HIGH, due_date=today)
    due_tomorrow = Task("Bath", 30, Priority.HIGH, due_date=today + timedelta(days=1))
    buddy.add_task(due_today)
    buddy.add_task(due_tomorrow)

    plan = Scheduler(owner).generate_plan(today)

    assert [item.task for item in plan.scheduled_items] == [due_today]
    assert plan.skipped_items == []


# --- Conflict detection ---------------------------------------------------


def test_detect_conflicts_flags_overlapping_preferred_times_across_pets():
    owner, buddy, mia = make_owner()
    buddy.add_task(Task("Walk", 30, Priority.HIGH, preferred_time=9 * 60))  # 9:00-9:30
    mia.add_task(Task("Meds", 10, Priority.HIGH, preferred_time=9 * 60 + 15))  # 9:15-9:25

    warnings = Scheduler(owner).detect_conflicts()

    assert len(warnings) == 1
    assert "Walk" in warnings[0] and "Meds" in warnings[0]


def test_detect_conflicts_flags_same_time_tasks_for_the_same_pet():
    owner, buddy, _ = make_owner()
    buddy.add_task(Task("Walk", 30, Priority.HIGH, preferred_time=9 * 60))
    buddy.add_task(Task("Flea treatment", 10, Priority.MEDIUM, preferred_time=9 * 60))

    warnings = Scheduler(owner).detect_conflicts()

    assert len(warnings) == 1
    assert "Walk (Buddy" in warnings[0] and "Flea treatment (Buddy" in warnings[0]


def test_detect_conflicts_ignores_back_to_back_and_completed_tasks():
    owner, buddy, mia = make_owner()
    buddy.add_task(Task("Walk", 30, Priority.HIGH, preferred_time=9 * 60))  # 9:00-9:30
    mia.add_task(Task("Meds", 10, Priority.HIGH, preferred_time=9 * 60 + 30))  # starts as walk ends
    done = Task("Play", 30, Priority.LOW, preferred_time=9 * 60)
    mia.add_task(done)
    done.mark_complete()

    assert Scheduler(owner).detect_conflicts() == []


def test_plan_moves_conflicting_task_to_next_free_slot():
    owner, buddy, mia = make_owner()
    buddy.add_task(Task("Walk", 30, Priority.HIGH, preferred_time=9 * 60))
    mia.add_task(Task("Meds", 10, Priority.MEDIUM, preferred_time=9 * 60))

    plan = Scheduler(owner).generate_plan()
    start_by_title = {item.task.title: item.start_minute for item in plan.scheduled_items}

    assert start_by_title["Walk"] == 9 * 60
    # Meds lost its 9:00 slot, so it goes in the earliest free slot (the 8:00 day start).
    assert start_by_title["Meds"] == 8 * 60


# --- Edge cases -----------------------------------------------------------


def test_plan_for_owner_with_no_pets_is_empty():
    plan = Scheduler(Owner("Jordan", available_minutes=60)).generate_plan()

    assert plan.scheduled_items == []
    assert plan.skipped_items == []
    assert plan.total_minutes == 0


def test_pet_with_no_tasks_adds_nothing_to_the_plan():
    owner, buddy, mia = make_owner()
    mia.add_task(Task("Meds", 5, Priority.HIGH))  # Buddy has no tasks at all

    plan = Scheduler(owner).generate_plan()

    assert [item.task.title for item in plan.scheduled_items] == ["Meds"]


def test_task_longer_than_available_time_is_skipped_with_a_reason():
    owner = Owner("Jordan", available_minutes=20)
    pet = Pet("Buddy", "dog")
    owner.add_pet(pet)
    pet.add_task(Task("Long hike", 30, Priority.HIGH))

    plan = Scheduler(owner).generate_plan()

    assert plan.scheduled_items == []
    assert "only 20 min left" in plan.skipped_items[0].reason


def test_higher_priority_task_wins_when_time_is_short():
    owner = Owner("Jordan", available_minutes=30)
    pet = Pet("Buddy", "dog")
    owner.add_pet(pet)
    low = Task("Brush", 30, Priority.LOW)
    high = Task("Meds", 30, Priority.HIGH)
    pet.add_task(low)  # added first, but lower priority
    pet.add_task(high)

    plan = Scheduler(owner).generate_plan()

    assert [item.task for item in plan.scheduled_items] == [high]
    assert [item.task for item in plan.skipped_items] == [low]


def test_task_finished_today_stays_on_the_plan_marked_done():
    owner, buddy, _ = make_owner()
    walk = Task("Walk", 30, Priority.HIGH, preferred_time=9 * 60)
    buddy.add_task(walk)
    walk.mark_complete()

    plan = Scheduler(owner).generate_plan()

    assert plan.skipped_items == []
    item = plan.scheduled_items[0]
    assert item.task is walk and item.done and item.start_minute == 9 * 60  # it keeps its slot
    assert "[done]" in plan.display()


def test_sort_by_time_handles_empty_list_and_does_not_change_the_original():
    assert Scheduler.sort_by_time([]) == []

    late = Task("Late", 10, Priority.LOW, preferred_time=18 * 60)
    early = Task("Early", 10, Priority.LOW, preferred_time=7 * 60)
    original = [late, early]

    Scheduler.sort_by_time(original)

    assert original == [late, early]


def test_sort_by_time_keeps_original_order_for_equal_times():
    first = Task("First", 10, Priority.LOW, preferred_time=9 * 60)
    second = Task("Second", 10, Priority.LOW, preferred_time=9 * 60)

    assert Scheduler.sort_by_time([first, second]) == [first, second]


def test_completing_an_already_completed_daily_task_does_not_duplicate_it():
    pet = Pet("Buddy", "dog")
    walk = Task("Walk", 30, Priority.HIGH, Frequency.DAILY)
    pet.add_task(walk)

    pet.complete_task(walk)
    second_result = pet.complete_task(walk)  # e.g. a double click

    assert second_result is None
    assert len(pet.tasks) == 2  # the original plus exactly one next occurrence


def test_detect_conflicts_reports_one_warning_per_overlapping_pair():
    owner, buddy, mia = make_owner()
    buddy.add_task(Task("Walk", 30, Priority.HIGH, preferred_time=9 * 60))
    buddy.add_task(Task("Brush", 30, Priority.LOW, preferred_time=9 * 60))
    mia.add_task(Task("Meds", 30, Priority.HIGH, preferred_time=9 * 60))

    # Three tasks all at 9:00 means three pairs: (Walk, Brush), (Walk, Meds), (Brush, Meds).
    assert len(Scheduler(owner).detect_conflicts()) == 3


def test_detect_conflicts_returns_empty_list_when_no_tasks_have_times():
    owner, buddy, _ = make_owner()
    buddy.add_task(Task("Cuddle", 10, Priority.LOW))

    assert Scheduler(owner).detect_conflicts() == []


def test_filter_tasks_for_unknown_pet_returns_empty_list():
    owner, buddy, _ = make_owner()
    buddy.add_task(Task("Walk", 30, Priority.HIGH))

    assert owner.filter_tasks(pet_name="Nobody") == []


def test_edit_task_changes_a_field_and_rejects_unknown_or_protected_fields():
    pet = Pet("Buddy", "dog")
    walk = Task("Walk", 30, Priority.LOW)
    pet.add_task(walk)

    pet.edit_task(walk, priority=Priority.HIGH, duration_minutes=45)

    assert walk.priority == Priority.HIGH and walk.duration_minutes == 45
    with pytest.raises(ValueError):
        pet.edit_task(walk, durration_minutes=10)  # typo'd field name
    with pytest.raises(ValueError):
        pet.edit_task(walk, id="hacked")  # id is managed by the system


# ---- day end and busy blocks ----

def _owner_with_one_pet(minutes=480):
    owner = Owner("Jordan", minutes)
    pet = Pet("Mia", "cat")
    owner.add_pet(pet)
    return owner, pet


def test_task_is_moved_out_of_a_busy_block():
    owner, pet = _owner_with_one_pet()
    owner.add_busy_block(9 * 60, 17 * 60)  # work 9-5
    pet.add_task(Task("Walk", 30, Priority.HIGH, preferred_time=10 * 60))  # during work
    item = Scheduler(owner).generate_plan().scheduled_items[0]
    assert item.start_minute == 8 * 60  # earliest free slot is before work


def test_task_waits_until_after_work_when_the_morning_is_blocked():
    owner, pet = _owner_with_one_pet()
    owner.add_busy_block(8 * 60, 17 * 60)  # busy from the day start until 5 PM
    pet.add_task(Task("Walk", 30, Priority.HIGH, preferred_time=10 * 60))
    assert Scheduler(owner).generate_plan().scheduled_items[0].start_minute == 17 * 60


def test_fallback_slot_skips_over_busy_block_when_it_will_not_fit_before():
    owner, pet = _owner_with_one_pet()
    owner.add_busy_block(8 * 60 + 15, 17 * 60)
    pet.add_task(Task("Long play", 30, Priority.HIGH))  # 30 min does not fit in 8:00-8:15
    assert Scheduler(owner).generate_plan().scheduled_items[0].start_minute == 17 * 60


def test_nothing_is_scheduled_past_the_day_end():
    owner, pet = _owner_with_one_pet()
    owner.day_end_minute = 8 * 60 + 30
    pet.add_task(Task("A", 20, Priority.HIGH))
    pet.add_task(Task("B", 20, Priority.LOW))
    plan = Scheduler(owner).generate_plan()
    assert [i.task.title for i in plan.scheduled_items] == ["A"]
    assert "no free time" in plan.skipped_items[0].reason


def test_preferred_time_past_day_end_falls_back_or_skips():
    owner, pet = _owner_with_one_pet()
    owner.day_end_minute = 20 * 60
    pet.add_task(Task("Late walk", 30, Priority.HIGH, preferred_time=19 * 60 + 45))  # would end 20:15
    item = Scheduler(owner).generate_plan().scheduled_items[0]
    assert item.start_minute == 8 * 60 and "preferred time" in item.reason


def test_defaults_have_no_day_end_or_busy_blocks():
    owner = Owner("Jordan", 60)
    assert owner.day_end_minute is None and owner.busy_blocks == []


def test_busy_block_validation_and_removal():
    owner = Owner("Jordan", 60)
    with pytest.raises(ValueError):
        owner.add_busy_block(600, 600)
    with pytest.raises(ValueError):
        owner.add_busy_block(700, 600)
    owner.add_busy_block(900, 1000)
    owner.add_busy_block(540, 600)
    assert owner.busy_blocks == [(540, 600), (900, 1000)]  # kept in time order
    owner.remove_busy_block((540, 600))
    assert owner.busy_blocks == [(900, 1000)]


# ---- the gap between tasks is spacing, not part of the time budget ----

def test_buffer_does_not_use_up_available_minutes():
    owner, pet = _owner_with_one_pet(minutes=30)
    owner.preferences.buffer_minutes = 5
    pet.add_task(Task("A", 15, Priority.HIGH))
    pet.add_task(Task("B", 15, Priority.HIGH))  # 15 + 15 = 30 minutes of tasks: exactly the budget
    plan = Scheduler(owner).generate_plan()
    assert len(plan.scheduled_items) == 2 and plan.skipped_items == []
    first, second = plan.scheduled_items
    assert second.start_minute - first.end_minute >= 5  # the gap is still kept as spacing


def test_skip_reason_names_the_owners_total_minutes():
    owner, pet = _owner_with_one_pet(minutes=20)
    pet.add_task(Task("Long hike", 30, Priority.HIGH))
    reason = Scheduler(owner).generate_plan().skipped_items[0].reason
    assert "needs 30 min but only 20 min left of your 20 minutes" in reason


# ---- finished tasks stay on the schedule ----

def test_done_task_keeps_its_slot_so_other_tasks_do_not_shift_into_it():
    owner, pet = _owner_with_one_pet()
    walk = Task("Walk", 30, Priority.HIGH)  # no preferred time: earliest slot, 8:00
    pet.add_task(walk)
    brush = Task("Brush", 15, Priority.LOW)
    pet.add_task(brush)
    before = {i.task.title: i.start_minute for i in Scheduler(owner).generate_plan().scheduled_items}

    walk.mark_complete()
    after = {i.task.title: i.start_minute for i in Scheduler(owner).generate_plan().scheduled_items}

    assert after == before  # marking done changed nothing about where anything sits


def test_done_task_is_never_dropped_even_when_time_is_tight():
    owner, pet = _owner_with_one_pet(minutes=40)
    done = Task("Low priority chore", 30, Priority.LOW)
    pet.add_task(done)
    done.mark_complete()
    pet.add_task(Task("Urgent", 30, Priority.HIGH))  # only 10 minutes remain after the done task

    plan = Scheduler(owner).generate_plan()

    assert [i.task.title for i in plan.scheduled_items] == ["Low priority chore"]  # the done one is kept
    assert "Urgent" in plan.skipped_items[0].task.title  # time already spent counts


def test_task_finished_on_an_earlier_day_is_not_on_todays_plan():
    owner, pet = _owner_with_one_pet()
    old = Task("Old", 10, Priority.LOW)
    pet.add_task(old)
    old.mark_complete()
    old.completed_on = date.today() - timedelta(days=1)

    plan = Scheduler(owner).generate_plan()

    assert plan.scheduled_items == [] and plan.skipped_items == []


def test_tasks_in_plan_covers_due_pending_and_finished_today_only():
    owner, pet = _owner_with_one_pet()
    due = Task("Due", 10, Priority.LOW)
    later = Task("Later", 10, Priority.LOW, due_date=date.today() + timedelta(days=2))
    done = Task("Done", 10, Priority.LOW)
    for t in (due, later, done):
        pet.add_task(t)
    done.mark_complete()
    assert Scheduler(owner).tasks_in_plan() == [due, done]


def test_conflicts_still_ignore_finished_tasks():
    owner, pet = _owner_with_one_pet()
    done = Task("Done", 30, Priority.HIGH, preferred_time=9 * 60)
    pet.add_task(done)
    done.mark_complete()
    pet.add_task(Task("Next", 30, Priority.HIGH, preferred_time=9 * 60))
    assert Scheduler(owner).detect_conflicts() == []


# ---- completing tasks: no endless copies ----

def test_a_task_that_is_not_due_yet_cannot_be_marked_done():
    pet = Pet("Buddy", "dog")
    tomorrows = Task("Walk", 30, Priority.HIGH, Frequency.DAILY, due_date=date.today() + timedelta(days=1))
    pet.add_task(tomorrows)

    with pytest.raises(ValueError, match="isn't due until"):
        pet.complete_task(tomorrows)

    assert tomorrows.completed is False and len(pet.tasks) == 1  # nothing changed


def test_completing_over_and_over_never_piles_up_copies():
    """The reported bug: every completion made a new copy due tomorrow, which could be completed again."""
    pet = Pet("Nala", "cat")
    feeding = Task("Morning Feeding", 15, Priority.HIGH, Frequency.DAILY)
    pet.add_task(feeding)

    first = pet.complete_task(feeding)
    for _ in range(5):  # keep trying to complete whatever is left
        for task in list(pet.tasks):
            if not task.completed:
                try:
                    pet.complete_task(task)
                except ValueError:
                    pass

    assert first.due_date == date.today() + timedelta(days=1)
    assert len(pet.tasks) == 2  # the done one and exactly one copy for tomorrow
    assert [t.completed for t in pet.tasks] == [True, False]


def test_mark_complete_counts_the_next_date_from_a_future_due_date():
    future = Task("Walk", 30, Priority.HIGH, Frequency.DAILY, due_date=date.today() + timedelta(days=3))
    nxt = future.mark_complete()  # called directly, bypassing Pet's guard
    assert nxt.due_date == date.today() + timedelta(days=4)  # never the same day as the task it came from


def test_completing_records_the_day_and_links_the_next_copy():
    pet = Pet("Buddy", "dog")
    walk = Task("Walk", 30, Priority.HIGH, Frequency.WEEKLY)
    pet.add_task(walk)
    nxt = pet.complete_task(walk)
    assert walk.completed_on == date.today() and walk.spawned_task_id == nxt.id
    assert nxt.completed_on is None and nxt.spawned_task_id is None  # the copy starts clean


def test_system_managed_fields_cannot_be_edited():
    pet = Pet("Buddy", "dog")
    walk = Task("Walk", 30, Priority.HIGH)
    pet.add_task(walk)
    for field_name in ("completed_on", "spawned_task_id", "id", "pet_name"):
        with pytest.raises(ValueError):
            pet.edit_task(walk, **{field_name: "x"})


# ---- undoing a completion ----

def test_reopening_a_repeating_task_removes_the_copy_it_created():
    pet = Pet("Buddy", "dog")
    walk = Task("Walk", 30, Priority.HIGH, Frequency.DAILY)
    pet.add_task(walk)
    pet.complete_task(walk)

    pet.reopen_task(walk)

    assert walk.completed is False and walk.completed_on is None and walk.spawned_task_id is None
    assert pet.tasks == [walk]  # back exactly as it was


def test_reopening_a_one_time_task_just_unchecks_it():
    pet = Pet("Buddy", "dog")
    vet = Task("Vet", 60, Priority.HIGH, Frequency.ONCE)
    pet.add_task(vet)
    pet.complete_task(vet)
    pet.reopen_task(vet)
    assert vet.completed is False and pet.tasks == [vet]


def test_reopening_can_be_followed_by_completing_again_without_extra_copies():
    pet = Pet("Buddy", "dog")
    walk = Task("Walk", 30, Priority.HIGH, Frequency.DAILY)
    pet.add_task(walk)
    for _ in range(3):
        pet.complete_task(walk)
        pet.reopen_task(walk)
    pet.complete_task(walk)
    assert len(pet.tasks) == 2  # still the original plus exactly one copy


def test_reopening_a_task_that_is_not_done_does_nothing_and_foreign_tasks_are_rejected():
    pet = Pet("Buddy", "dog")
    walk = Task("Walk", 30, Priority.HIGH)
    pet.add_task(walk)
    pet.reopen_task(walk)
    assert walk.completed is False and pet.tasks == [walk]
    with pytest.raises(ValueError):
        pet.reopen_task(Task("Other", 5, Priority.LOW))
