"""Tests for the calendar drawing (pure HTML, no Streamlit needed)."""

from calendar_view import render_calendar_html


def task(start, end, title="Feed", pet="Nala", priority="high"):
    return {"kind": "task", "start": start, "end": end, "title": title, "pet": pet,
            "priority": priority, "reason": "placed at its preferred time"}


def busy(start, end):
    return {"kind": "busy", "start": start, "end": end}


def test_nothing_to_draw_returns_empty():
    assert render_calendar_html([], ["Nala"], 480, None) == ""


def test_blocks_show_their_text_not_just_colour():
    html = render_calendar_html([task(480, 510, "Morning walk")], ["Nala"], 480, None)
    assert "Morning walk" in html and "8:00 AM – 8:30 AM" in html and "High priority" in html
    assert "Nala" in html  # legend and block both name the pet


def test_each_pet_gets_its_own_colour():
    html = render_calendar_html([task(480, 500, pet="Nala"), task(600, 620, pet="Mocha")], ["Nala", "Mocha"], 480, None)
    assert "#cfe8ff" in html and "#ffd9c2" in html


def test_hour_labels_use_standard_time():
    html = render_calendar_html([task(480, 500), task(13 * 60, 13 * 60 + 20)], ["Nala"], 480, None)
    assert ">8 AM<" in html and ">12 PM<" in html and ">1 PM<" in html
    assert ">13<" not in html


def test_long_busy_block_is_squeezed_and_labelled():
    tall = render_calendar_html([task(480, 500), busy(540, 1020), task(1020, 1040)], ["Nala"], 480, None)
    assert "Busy (not to scale)" in tall and "9:00 AM – 5:00 PM" in tall
    # An 8-hour busy block drawn to scale would be over 600px tall at this zoom; squeezed it is not.
    height = float(tall.split("height:")[1].split("px")[0])
    assert height < 1000


def test_short_busy_block_stays_to_scale():
    html = render_calendar_html([task(480, 500), busy(540, 600)], ["Nala"], 480, None)
    assert "not to scale" not in html and "<strong>Busy</strong>" in html


def test_titles_are_escaped():
    html = render_calendar_html([task(480, 500, title="<script>alert(1)</script>")], ["Nala"], 480, None)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_day_end_extends_the_grid():
    short = render_calendar_html([task(480, 500)], ["Nala"], 480, None)
    long = render_calendar_html([task(480, 500)], ["Nala"], 480, 20 * 60)
    assert ">8 PM<" in long and ">8 PM<" not in short


def test_task_before_day_start_is_still_drawn():
    html = render_calendar_html([task(420, 440, "Early meds")], ["Nala"], 480, None)
    assert "Early meds" in html and ">7 AM<" in html


# ---- pet names on every block, and finished tasks ----

def test_short_blocks_still_show_the_pet_name():
    """A 5-minute block is too short for two lines; it used to show the bare title with no pet."""
    html = render_calendar_html([task(480, 485, "Quick meds", pet="Nala")], ["Nala"], 480, None)
    assert "Quick meds</strong> (Nala)" in html


def test_every_block_size_names_the_pet():
    short = render_calendar_html([task(480, 485, "A", pet="Mocha")], ["Nala", "Mocha"], 480, None)
    tall = render_calendar_html([task(480, 600, "B", pet="Mocha")], ["Nala", "Mocha"], 480, None)
    assert "(Mocha)" in short and "(Mocha)" in tall


def test_done_task_is_struck_through_with_a_check_and_the_word_done():
    done = {**task(480, 510, "Feed", pet="Nala"), "done": True}
    html = render_calendar_html([done], ["Nala"], 480, None)
    assert "✓ " in html and "line-through" in html and "✓ Done" in html
    assert "[done]" in html  # also in the hover text


def test_unfinished_task_has_no_check_or_strike():
    html = render_calendar_html([task(480, 510, "Feed")], ["Nala"], 480, None)
    assert "✓" not in html and "line-through" not in html


def test_done_short_block_keeps_pet_check_and_done_label():
    done = {**task(480, 485, "Meds", pet="Nala"), "done": True}
    html = render_calendar_html([done], ["Nala"], 480, None)
    assert "✓ " in html and "(Nala)" in html and "Done" in html
