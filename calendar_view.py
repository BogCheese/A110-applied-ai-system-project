"""Draw a day's schedule as Google-Calendar-style time blocks.

`render_calendar_html()` takes plain data (no Streamlit) and returns an HTML/CSS
string, so it can be tested on its own and shown with `st.html()`.

How the picture works:
- Time runs top to bottom. Each task is a coloured block whose top and height match
  its start time and length. The colour shows which pet it is for.
- Long busy stretches (like work 9-5) would fill the whole screen if drawn to scale,
  so any busy block of two hours or more is squeezed into a fixed-height grey band,
  labelled with its real times. Everything else is to scale.
- Every block carries its text (title, time, priority), so nothing depends on colour
  alone, and the page also offers the same schedule as a plain list.
"""

from __future__ import annotations

from html import escape

from pawpal_system import format_time

# (background, accent) per pet, in the order pets were added. Dark text on every
# background keeps the contrast high.
PET_COLORS = [
    ("#cfe8ff", "#2f7fd1"),  # blue
    ("#ffd9c2", "#e0762f"),  # orange
    ("#d4f1d0", "#3d9a44"),  # green
    ("#f3d6ff", "#9b4fc4"),  # purple
    ("#fff2b3", "#b89a00"),  # yellow
    ("#ffd0dc", "#d6406b"),  # pink
]
PRIORITY_ICONS = {"high": "🔴", "medium": "🟡", "low": "🟢"}
TEXT_COLOR = "#1b1b1b"

AXIS_PX = 64  # width of the hour labels on the left
COLLAPSE_AT_MINUTES = 120  # busy blocks at least this long are squeezed
COLLAPSED_BAND_PX = 64  # height of a squeezed busy block
TARGET_VISIBLE_PX = 640  # roughly how tall the to-scale part should be
MIN_PX_PER_MINUTE, MAX_PX_PER_MINUTE = 1.3, 4.0
COMPACT_BLOCK_PX = 42  # blocks shorter than this show a single line of text


def _hour_label(hour: int) -> str:
    return f"{hour % 12 or 12} {'AM' if hour % 24 < 12 else 'PM'}"


def render_calendar_html(blocks: list[dict], pet_names: list[str], day_start: int, day_end: int | None) -> str:
    """Return the schedule as an HTML/CSS time grid.

    blocks: dicts with kind ("task" or "busy"), start and end (minutes since midnight),
    and for tasks: title, pet, priority ("high"/"medium"/"low") and reason.
    Returns "" when there is nothing to draw.
    """
    if not blocks:
        return ""

    first = min([b["start"] for b in blocks] + [day_start])
    last = max([b["end"] for b in blocks] + ([day_end] if day_end is not None else []))
    range_start = (first // 60) * 60
    range_end = -(-last // 60) * 60  # round up to the next whole hour

    collapsed = sorted(
        (b["start"], b["end"])
        for b in blocks
        if b["kind"] == "busy" and b["end"] - b["start"] >= COLLAPSE_AT_MINUTES
    )
    collapsed_minutes = sum(end - start for start, end in collapsed)
    visible_minutes = max(1, (range_end - range_start) - collapsed_minutes)
    scale = min(MAX_PX_PER_MINUTE, max(MIN_PX_PER_MINUTE, TARGET_VISIBLE_PX / visible_minutes))

    def y(minute: int) -> float:
        """Pixels from the top for a time, counting each squeezed busy block as a fixed band."""
        pixels, cursor = 0.0, range_start
        for start, end in collapsed:
            if minute <= start:
                return pixels + (minute - cursor) * scale
            pixels += (start - cursor) * scale
            if minute < end:
                return pixels + (minute - start) / (end - start) * COLLAPSED_BAND_PX
            pixels += COLLAPSED_BAND_PX
            cursor = end
        return pixels + (minute - cursor) * scale

    height = y(range_end) + 6
    parts: list[str] = []

    # Hour lines and labels (skipping hours hidden inside a squeezed busy band).
    for hour in range(range_start // 60, range_end // 60 + 1):
        minute = hour * 60
        if any(start < minute < end for start, end in collapsed):
            continue
        top = y(minute)
        parts.append(
            f'<div style="position:absolute;left:0;right:0;top:{top:.1f}px;'
            'border-top:1px solid rgba(128,128,128,0.28);"></div>'
            f'<div style="position:absolute;left:-{AXIS_PX}px;width:{AXIS_PX - 10}px;top:{top - 8:.1f}px;'
            f'text-align:right;font-size:12px;line-height:16px;opacity:0.85;">{_hour_label(hour)}</div>'
        )

    pet_index = {name: i for i, name in enumerate(pet_names)}

    for block in sorted(blocks, key=lambda b: b["start"]):
        top = y(block["start"])
        block_height = max(y(block["end"]) - top - 1, 4)
        times = f"{format_time(block['start'])} – {format_time(block['end'])}"
        compact = block_height < COMPACT_BLOCK_PX

        if block["kind"] == "busy":
            squeezed = (block["start"], block["end"]) in collapsed
            label = "Busy" + (" (not to scale)" if squeezed else "")
            background = "repeating-linear-gradient(45deg,#e6e6e6,#e6e6e6 8px,#d6d6d6 8px,#d6d6d6 16px)"
            accent, tooltip = "#777777", f"Busy {times}"
            body = f"<strong>{label}</strong> · {times}"
        else:
            background, accent = PET_COLORS[pet_index.get(block["pet"], 0) % len(PET_COLORS)]
            icon = PRIORITY_ICONS.get(block["priority"], "")
            priority = f"{icon} {block['priority'].title()} priority"
            done = bool(block.get("done"))
            # A finished task keeps its place but gets a check mark, a struck-through title and "Done".
            check = "✓ " if done else ""
            strike = ' style="text-decoration:line-through"' if done else ""
            title = f"<strong{strike}>{escape(block['title'])}</strong>"
            pet = escape(block["pet"])
            done_note = " · ✓ Done" if done else ""
            tooltip = (
                f"{block['title']} ({block['pet']}){' [done]' if done else ''} {times}, {priority}. "
                f"{block.get('reason', '')}"
            )
            if compact:
                # Short blocks still name the pet, so you can tell whose task it is at a glance.
                body = f"{check}{title} ({pet}) · {times}{done_note}"
            else:
                body = (
                    f"{check}{title} <span style=\"opacity:0.85\">({pet})</span><br>"
                    f"<span style=\"font-size:12px\">{times} · {priority}{done_note}</span>"
                )

        parts.append(
            f'<div title="{escape(tooltip, quote=True)}" style="position:absolute;left:6px;right:0;'
            f"top:{top:.1f}px;height:{block_height:.1f}px;box-sizing:border-box;overflow:hidden;"
            f"background:{background};border-left:4px solid {accent};border-radius:4px;color:{TEXT_COLOR};"
            f"padding:{'0 6px' if compact else '4px 8px'};font-size:{'12px' if compact else '13px'};"
            f"line-height:{f'{min(block_height, 18):.0f}px' if compact else '17px'};"
            f"{'white-space:nowrap;text-overflow:ellipsis;' if compact else ''}"
            f'">{body}</div>'
        )

    legend = "".join(
        f'<span style="display:inline-block;margin:0 8px 6px 0;padding:2px 10px;border-radius:12px;'
        f"background:{PET_COLORS[i % len(PET_COLORS)][0]};border:1px solid {PET_COLORS[i % len(PET_COLORS)][1]};"
        f'color:{TEXT_COLOR};font-size:13px;">{escape(name)}</span>'
        for i, name in enumerate(pet_names)
    )

    return (
        '<div role="group" aria-label="Day schedule calendar">'
        f'<div style="margin-bottom:6px;">{legend}</div>'
        f'<div style="position:relative;height:{height:.0f}px;margin-left:{AXIS_PX}px;'
        'border-left:1px solid rgba(128,128,128,0.4);">'
        + "".join(parts)
        + "</div></div>"
    )
