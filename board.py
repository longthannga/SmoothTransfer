"""Drag-and-drop quarter board. Students move course cards between quarters;
plain Python re-checks the arrangement live (unit limits, sequence order)."""
import json
import re

import streamlit as st
from streamlit_sortables import sort_items

from validator import check_plan
from workload import HOURS_PER_UNIT, quarter_hours, quarter_totals

# One compact row per quarter. Left: the quarter name with its units under it. Middle: the cards.
# Right: the running total of units by the end of that quarter, and the estimated study hours a week.
# The numbers are drawn by CSS (see totals_css) so they can follow the cards as the student drags them.
CARD_STYLE = """
.sortable-component { gap: 6px; }
.sortable-container { background: #eef0f4 !important; border-radius: 8px; padding: 6px 10px; min-width: 0;
                      display: flex !important; flex-direction: row !important; align-items: center; gap: 12px; }
.sortable-container-header { background: #eef0f4 !important; color: #111827 !important; font-weight: 700;
                             font-size: 14px; padding: 0; flex: 0 0 152px; white-space: nowrap; line-height: 1.3; }
.sortable-container-header::after { display: block; font-weight: 400; font-size: 12px; color: #4b5563;
                                    white-space: normal; }
.sortable-container-body, .sortable-container-boy { background: #eef0f4 !important; flex: 1 1 auto;
                             display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-height: 36px; }
.sortable-item { background: #ffffff !important; color: #111827 !important; border-top: 3px solid #60a5fa;
                 border-radius: 8px; padding: 5px 10px; margin: 0 !important; font-size: 14px; list-style: none; }
.sortable-container::before { order: 2; flex: 0 0 auto; min-width: 118px; text-align: right; color: #374151;
                              font-size: 13px; white-space: nowrap; }
.sortable-container::after { order: 3; flex: 0 0 auto; min-width: 88px; text-align: right; color: #111827;
                             font-weight: 700; font-size: 14px; white-space: nowrap; }
"""

_SHORT = {"meets the minimum": "meets min", "below the minimum": "below min",
          "below the minimum (last quarter)": "below min (last quarter)"}


def total_text(units):
    return f"{units:g} units"


def row_texts(rows, min_units=0):
    """For each quarter row (from workload.quarter_totals): the line under its name, the running total, and
    the weekly hours. These are the three pieces of text shown around the cards."""
    out = []
    for r in rows:
        under = "no classes" if r["status"] == "no classes" else total_text(r["units"])
        if min_units and r["status"] not in ("", "no classes"):
            under += f" · {_SHORT.get(r['status'], r['status'])}"
        out.append((under, f"{r['total_by_end']:g} units by end", f"~{r['weekly_hours']:g} hrs/wk"))
    return out


def css_string(text):
    """A CSS string literal for `content`. json.dumps is NOT safe here: it writes "·" as \\u00b7, and CSS
    reads that as the letters u00b7. Backslashes, quotes, line breaks and "<" are escaped the CSS way."""
    t = (str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\A ").replace("<", "\\3C "))
    return f'"{t}"'


def totals_css(texts):
    """CSS that writes the three pieces of text for each quarter. `texts` is one (under the name, running total,
    hours) triple per quarter, in order. The quarters are the numbered boxes of the widget, so box 1 is the first."""
    out = []
    for i, (under, end, hours) in enumerate(texts, start=1):
        row = f".sortable-container:nth-of-type({i})"
        out.append(f"{row} .sortable-container-header:after {{ content: {css_string(under)}; }}")
        out.append(f"{row}:before {{ content: {css_string(end)}; }}")
        out.append(f"{row}:after {{ content: {css_string(hours)}; }}")
    return "\n".join(out)


def card_label(c):
    """Text on a course card. A Cal-GETC placeholder shows its kind of course and 4-6 units."""
    if c.get("placeholder"):
        return f"{c['title']} · {c['units_label']} units"
    return f"{c['code']} · {c['units']:g} units"


def workload_for(total, max_units):
    """Simple rule used after the student rearranges: share of their unit limit."""
    share = total / max_units if max_units else 1
    return "light" if share <= 0.6 else "moderate" if share <= 0.9 else "heavy"


def order_warnings(quarters):
    """Warn when a lettered sequence (MATH 1A, 1B, 1C) is out of order or in the same quarter."""
    where = {}
    for i, q in enumerate(quarters):
        for c in q["courses"]:
            m = re.match(r"^(.*\d+)([A-Z])$", c["code"])
            if m:
                where[(m.group(1), m.group(2))] = i
    out = []
    for (base, letter), i in where.items():
        for (b2, l2), j in where.items():
            if b2 == base and l2 > letter and j <= i:
                out.append(f"{base}{l2} is in the same quarter or earlier than {base}{letter}. "
                           "Check the prerequisite.")
    return sorted(set(out))


def render_board(res, reqs, completed, max_units, min_units=0, units_before=0.0, units_note=""):
    """Show the draggable board. Returns True if the plan was changed and saved."""
    plan = res["plan"]
    by_label, containers = {}, []
    for q in plan["quarters"]:
        items = []
        for c in q["courses"]:
            label = card_label(c)
            by_label[label] = c
            items.append(label)
        containers.append({"header": q["term"], "items": items})

    # a new key whenever the saved plan changes, so the board resets to it
    key = "board-" + str(hash(json.dumps(plan["quarters"], sort_keys=True, default=str)))
    # The numbers on each row are part of the widget's style. They start from the saved plan, and when the
    # student drags a card the style is updated once to match (see below).
    shown_key = key + "-totals"
    shown = st.session_state.get(shown_key) or row_texts(quarter_totals(plan["quarters"], units_before, min_units),
                                                         min_units)
    st.caption("Each row shows its units (under the quarter name), your running total of units by the end of "
               f"that quarter, and about how many hours a week it takes ({HOURS_PER_UNIT} hours per unit).")
    new = sort_items(containers, multi_containers=True, direction="horizontal",
                     custom_style=CARD_STYLE + totals_css(shown), key=key)

    quarters = [{"term": g["header"], "courses": [by_label[l] for l in g["items"]]} for g in new]
    for q in quarters:
        q["total_units"] = sum(c["units"] for c in q["courses"])
    rows = quarter_totals(quarters, units_before, min_units)   # follows the cards live, as they are dragged
    st.session_state["board_rows"] = rows
    live = row_texts(rows, min_units)
    if live != shown:                      # the cards moved: redraw the numbers once, then stop
        st.session_state[shown_key] = live
        st.rerun()
    st.caption(units_note or "Totals count only the quarters in this plan.")

    problems = check_plan({"quarters": quarters}, reqs, completed, max_units, min_units) + order_warnings(quarters)
    for p in problems:
        st.warning(p)
    if not problems:
        st.success("This arrangement passes the checker.")

    moved = [[c["code"] for c in q["courses"]] for q in quarters] != \
            [[c["code"] for c in q["courses"]] for q in plan["quarters"]]
    if moved and st.button("Save my arrangement", type="primary"):
        by_term = {q["term"]: q for q in plan["quarters"]}
        for q in quarters:
            old = by_term[q["term"]]
            old["courses"] = q["courses"]
            old["total_units"] = q["total_units"]
            old["workload"] = workload_for(q["total_units"], max_units)
            old.update(quarter_hours(q["courses"]))
            old["workload_note"] = (f"Rearranged by you: {q['total_units']:g} of {max_units} units, "
                                    f"about {old['weekly_hours']:g} hours a week.")
        res["issues_remaining"] = problems
        return True
    return False