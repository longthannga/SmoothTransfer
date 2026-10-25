"""Drag-and-drop Cal-GETC worksheet.

Courses are placed for the student from Foothill's official Cal-GETC list (calgetc_list.py): finished
courses, courses in progress, and the courses the major still needs. The student can drag any card to
change it, and every placement is checked against the list. Python only counts what was placed, so this
is a worksheet, not a certification.

Placements are saved per profile in the SQLite table cal_getc_courses (created here on first use).
"""
import json
import time
from collections import Counter

import pandas as pd
import streamlit as st
from streamlit_sortables import sort_items

import calgetc_list as gcl
import store
from cal_getc import CAL_GETC_AREAS
from settings import get_setting, set_setting
from terms import term_label
from transcript_match import PASSING, BELOW_C, parse_note

# Layout, tested in a real browser. This package lays its boxes out side by side only when the
# direction is "vertical" (that is how its own CSS works), so that is what we use. The first box
# (your courses) gets a full-width row of cards, and the areas follow in rows underneath.
# Keep these rules simple: when I made the boxes equal height (flex column / stretch), the drag
# widget crashed with a React error after a few drags.
DIRECTION = "vertical"
POOL = "Your courses"
NOT_PLACED = "Not placed"

STYLE = """
.sortable-component.vertical { gap: 12px; padding: 4px; align-items: flex-start !important; }
.sortable-component.vertical .sortable-container { flex: 0 0 215px !important; background: #eef0f4 !important;
  border-radius: 10px; padding: 8px !important; margin: 0 !important; min-width: 0 !important; }
.sortable-component.vertical style + .sortable-container { flex: 1 1 100% !important; }
.sortable-component.vertical style + .sortable-container .sortable-container-body { display: flex; flex-wrap: wrap; }
.sortable-component.vertical style + .sortable-container .sortable-item { display: inline-block !important; }
.sortable-container-header { background: #eef0f4 !important; color: #111827 !important; font-weight: 700; padding: 6px 4px; }
.sortable-container-body, .sortable-container-boy { background: #eef0f4 !important; min-height: 84px; }
.sortable-item { background: #ffffff !important; color: #111827 !important; border-top: 3px solid #60a5fa;
  border-radius: 8px; padding: 8px 10px; margin: 4px; }
"""


# ---------- pure logic (no Streamlit needed) ----------

def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def area_header(area):
    """Container title, for example '4 · Social and Behavioral Sciences'."""
    return f"{area['id']} · {area['name'].split(': ', 1)[-1]}"


def placeable(saved_courses):
    """Sort the student's saved course records into courses they can place and courses they cannot.

    Placeable: passed with a C or better (or P/CR), in progress, or typed in by hand.
    Not placeable: below C, failed, withdrawn. One entry per course code (the best attempt wins).
    Returns {"courses": [...], "blocked": [(code, reason)]}."""
    rank = {"done": 0, "in_progress": 1, "blocked": 2}
    best = {}
    for r in saved_courses:
        code = (r.get("course_code") or "").strip().upper()
        if not code:
            continue
        grade, kind = parse_note(r.get("notes", ""))
        reason = ""
        if r.get("source") != "transcript_pdf":        # typed in by the student
            state = "done"
        elif kind == "in_progress" or grade == "IP":
            state = "in_progress"
        elif grade in PASSING:
            state = "done"
        elif grade in BELOW_C:
            state, reason = "blocked", f"grade {grade} is below C"
        else:
            state, reason = "blocked", f"grade {grade or 'missing'}, not passed"
        units = _num(r.get("units"))
        label = f"{code} · {units:g} units" if units else code
        if state == "in_progress":
            label += " · in progress"
        elif kind == "ap_ib_or_transfer":
            label += " · AP/transfer"
        entry = {"key": code, "code": code, "title": r.get("title") or "", "units": units,
                 "grade": grade, "kind": kind, "state": state, "term": r.get("term") or "",
                 "label": label, "reason": reason}
        if code not in best or rank[state] < rank[best[code]["state"]]:
            best[code] = entry
    courses = [e for e in best.values() if e["state"] != "blocked"]
    blocked = [(e["code"], e["reason"]) for e in best.values() if e["state"] == "blocked"]
    return {"courses": courses, "blocked": blocked}


def build_containers(courses, placements):
    """The drag-and-drop layout: the pool of unplaced courses, then one box per Cal-GETC area."""
    area_ids = {a["id"] for a in CAL_GETC_AREAS}
    pool, by_area = [], {a["id"]: [] for a in CAL_GETC_AREAS}
    for c in courses:
        area = placements.get(c["key"])
        (by_area[area] if area in area_ids else pool).append(c["label"])
    return ([{"header": POOL, "items": pool}] +
            [{"header": area_header(a), "items": by_area[a["id"]]} for a in CAL_GETC_AREAS])


def read_arrangement(groups, courses):
    """{course key: area id} from the layout the student arranged. Courses left in the pool are not placed."""
    label_to_key = {c["label"]: c["key"] for c in courses}
    header_to_area = {area_header(a): a["id"] for a in CAL_GETC_AREAS}
    out = {}
    for g in groups or []:
        area = header_to_area.get(g.get("header"))
        if area:
            for label in g.get("items", []):
                if label in label_to_key:
                    out[label_to_key[label]] = area
    return out


def _tag(c, suffix=""):
    extra = [x for x in (suffix, "planned" if c["state"] == "planned" else "") if x]
    return c["code"] + (f" ({', '.join(extra)})" if extra else "")


def summarize(placements, courses, lab=()):
    """One row per area: how many courses are placed against how many are usually needed.

    "taken" counts courses the student has finished or is taking. "planned" counts courses the major still
    needs, which the student has not taken. "placed" is both together. `lab` holds the keys of placed
    science courses that carry the lab, which count toward 5C."""
    by_key = {c["key"]: c for c in courses}
    rows = []
    for a in CAL_GETC_AREAS:
        direct = [by_key[k] for k, area in placements.items() if area == a["id"] and k in by_key]
        mine, names = list(direct), [_tag(c) for c in direct]
        if a["id"] == "5C":                       # a science course with a lab counts here too
            extra = [by_key[k] for k in sorted(lab) if k in by_key and k in placements]
            mine += extra
            names += [_tag(c, "lab") for c in extra]
        need = int(a["courses"])
        taken = sum(c["state"] != "planned" for c in mine)
        rows.append({"id": a["id"], "name": a["name"], "needed": need, "placed": len(mine),
                     "taken": taken, "planned": len(mine) - taken,
                     "units": sum(c["units"] for c in direct),
                     "in_progress": sum(c["state"] == "in_progress" for c in mine),
                     "courses": names, "enough": len(mine) >= need, "enough_taken": taken >= need})
    return rows


def area_status(r):
    """'Enough placed' only when courses the student has taken or is taking are enough. Courses that are only
    planned give 'Enough with your plan'."""
    if r["enough_taken"]:
        return "Enough placed"
    if r["enough"]:
        return "Enough with your plan"
    return f"Needs {r['needed'] - r['placed']} more"


def planned_entries(needed, listing, taken):
    """Courses the major still needs, as cards for the board. Only courses on the official list are shown,
    and a course already on the transcript (done or in progress) is not shown twice."""
    have = {gcl.course_key(c["code"]) for c in taken}
    out = []
    for n in needed:
        if n.get("placeholder"):
            continue
        code = (n.get("code") or "").strip().upper()
        if not code or gcl.course_key(code) in have or not gcl.entry_for(listing, code):
            continue
        units = _num(n.get("units"))
        out.append({"key": code, "code": code, "title": n.get("title") or "", "units": units, "grade": "",
                    "kind": "planned", "state": "planned", "term": "", "reason": "",
                    "label": (f"{code} · {units:g} units · planned" if units else f"{code} · planned")})
    return out


def check_placements(courses, placements, listing):
    """Every placed course against the official list. Returns rows {course, area, ok, says}."""
    rows = []
    for c in courses:
        area = placements.get(c["key"])
        if not area:
            continue
        info = gcl.areas_for(listing, c["code"], c.get("term", ""))
        names = ", ".join(a for a in info["areas"]) or "none"
        if area in info["areas"]:
            ok, says = True, "On the list for this area"
        elif info["status"] == "not_listed":
            ok, says = False, "Not on Foothill's Cal-GETC list"
        elif info["status"] == "not_approved_then":
            ok, says = False, "On the list, but not approved for the quarter it was taken"
        else:
            ok, says = False, f"The list names Area {names} for this course, not {area}"
        rows.append({"course": c["code"], "area": area, "ok": ok, "says": says})
    return rows


def why_unplaced(courses, placements, listing):
    """For each course left in the pool, what the official list says about it."""
    out = {}
    for c in courses:
        if placements.get(c["key"]):
            continue
        info = gcl.areas_for(listing, c["code"], c.get("term", ""))
        if info["status"] == "not_listed":
            out[c["code"]] = "Not on Foothill's Cal-GETC list, so it does not count for any area"
        elif info["status"] == "not_approved_then":
            out[c["code"]] = "On the list, but not for the quarter it was taken"
        else:
            out[c["code"]] = (f"On the list for Area {', '.join(info['areas'])}, but not placed "
                              "(those areas already have enough courses, or you moved it out)")
    return out


# ---------- what is still missing: placeholders for the plan ----------

PLACEHOLDER_UNITS = (4, 6)   # quarter units a Cal-GETC course usually has
PLAN_UNITS = 5               # what the planner counts for each placeholder (the real course may be 4 to 6)
NO_PLACEHOLDER = {"5C"}      # the lab is normally part of a 5A or 5B science course, not a separate course


def _short(area):
    return area["name"].split(": ", 1)[-1]


def missing_slots(placements, courses):
    """The Cal-GETC courses still needed: for each area, the usual count minus the courses placed there.
    Courses placed but still in progress count as placed."""
    keys = {c["key"] for c in courses}
    have = Counter(a for k, a in placements.items() if k in keys)
    slots = []
    for a in CAL_GETC_AREAS:
        if a["id"] in NO_PLACEHOLDER:
            continue
        missing = max(int(a["courses"]) - have.get(a["id"], 0), 0)
        for n in range(1, missing + 1):
            slots.append({"area": a["id"], "short": _short(a), "n": n, "of": missing})
    return slots


def slot_title(slot):
    title = f"{slot['short']} course"
    if slot["of"] > 1:
        title += f" ({slot['n']} of {slot['of']})"
    return f"{title} · Cal-GETC {slot['area']}"


def placeholder_reqs(slots, names=None):
    """One planner requirement per missing course. Each can only be met by its own placeholder
    course, so the planner, the checker and the board treat it like any other course."""
    lo, hi = PLACEHOLDER_UNITS
    names = names or {}
    out = []
    for s in slots:
        code = f"CAL-GETC {s['area']} #{s['n']}"
        generic = slot_title(s)
        name = (names.get(code) or "").strip()   # the student's own name for this course, if they gave one
        out.append({
            "course": f"Cal-GETC {s['area']} #{s['n']}",
            "title": f"{name} · Cal-GETC {s['area']}" if name else generic, "generic_title": generic,
            "units": PLAN_UNITS,
            "section_id": f"calgetc-{s['area']}-{s['n']}", "pick_n_of_section": None, "status": "articulated",
            "options": [[{"code": code, "units": PLAN_UNITS}]], "note": None,
            "placeholder": True, "units_min": lo, "units_max": hi, "area_id": s["area"], "short": s["short"],
        })
    return out


def apply_names(plan, names):
    """Update the placeholder cards already in a plan (also in finished quarters) to the student's names.
    A card with no name goes back to its generic title, like 'Arts course · Cal-GETC 3A'."""
    names = names or {}
    for key in ("quarters", "past_quarters"):
        for q in plan.get(key, []):
            for c in q.get("courses", []):
                if not c.get("placeholder"):
                    continue
                area = c.get("area_id") or (c["code"].split()[1] if len(c["code"].split()) > 1 else "")
                generic = c.get("generic_title") or c.get("title") or c["code"]
                c.setdefault("generic_title", generic)
                name = (names.get(c["code"]) or "").strip()
                c["title"] = f"{name} · Cal-GETC {area}" if name else c["generic_title"]
    return plan


def units_so_far(saved_courses):
    """(earned, in_progress) units from the student's saved course records. Earned counts courses passed
    with a C or better (including AP/transfer credit Foothill accepted) and courses typed in by hand."""
    courses = placeable(saved_courses)["courses"]
    return (sum(c["units"] for c in courses if c["state"] == "done"),
            sum(c["units"] for c in courses if c["state"] == "in_progress"))


# ---------- saving ----------

def _db():
    store.db.execute("CREATE TABLE IF NOT EXISTS cal_getc_courses(profile_code TEXT NOT NULL, "
                     "course_key TEXT NOT NULL, area_id TEXT NOT NULL, updated_at REAL, "
                     "PRIMARY KEY(profile_code, course_key))")
    return store.db


def load_placements(profile_code):
    rows = _db().execute("SELECT course_key, area_id FROM cal_getc_courses WHERE profile_code=?",
                         (profile_code,)).fetchall()
    return {r["course_key"]: r["area_id"] for r in rows}


def save_placements(profile_code, placements):
    """Replace this profile's placements with the given ones."""
    valid = {a["id"] for a in CAL_GETC_AREAS}
    db = _db()
    db.execute("DELETE FROM cal_getc_courses WHERE profile_code=?", (profile_code,))
    db.executemany("INSERT INTO cal_getc_courses(profile_code, course_key, area_id, updated_at) VALUES (?,?,?,?)",
                   [(profile_code, k, a, time.time()) for k, a in placements.items() if a in valid])
    db.commit()


def placed_by_area(profile_code, saved_courses):
    """{area id: [course codes]} for the saved placements that still match the student's courses."""
    if not profile_code:
        return {}
    keys = {c["key"] for c in placeable(saved_courses)["courses"]}
    out = {}
    for k, a in load_placements(profile_code).items():
        if k in keys:
            out.setdefault(a, []).append(k)
    return {a: sorted(v) for a, v in out.items()}


# ---------- Streamlit ----------

def render_cal_getc_board(profile_code, saved_courses, ensure_profile, pending=None, on_use_pending=None,
                          needed=(), list_info=None, on_reload=None, plan_start=""):
    """The board. `ensure_profile()` creates a profile when none is open (called only on save).
    `pending` is a transcript the student already uploaded and reviewed but has not saved yet
    ({"name", "rows"}). `on_use_pending()` saves it, so the student never has to upload twice.
    `needed` is what the major still requires (planner.needed_courses). `list_info` is the result of
    calgetc_list.load_list. With it the courses are placed for the student; without it they place them by hand."""
    info = placeable(saved_courses)
    listing = (list_info or {}).get("listing")
    courses = info["courses"] + (planned_entries(needed, listing, info["courses"]) if listing else [])
    st.markdown("**Place your courses**")
    if not info["courses"] and pending and on_use_pending:     # uploaded once above but not saved yet
        st.info(f"You uploaded {pending['name']} ({len(pending['rows'])} courses) but have not saved it yet. "
                "The board uses the courses in your records.")
        if st.button("Save the transcript I uploaded and use it here", key="gcb_use_pending"):
            on_use_pending()
            st.rerun()
    if not courses:
        if not (pending and on_use_pending):
            st.info("The board uses the courses in your records. Upload a transcript or add courses by hand "
                    "under “Your academic record” above, then place them here.")
        return {"saved": {}, "live": {}, "dirty": False, "courses": [], "list_ok": bool(listing)}

    keys = {c["key"] for c in courses}
    full = load_placements(profile_code) if profile_code else {}
    saved = {k: a for k, a in full.items() if k in keys}
    done = set(get_setting(profile_code, "gc_auto_done", []) or []) if profile_code else set()

    # ---- place courses from the official list: once per course, so the student's own moves are never undone ----
    auto = {}
    if listing:
        todo = [c for c in courses if c["key"] not in saved and c["key"] not in done]
        if todo:
            orphans = {gcl.course_key(k): a for k, a in full.items() if k not in keys}   # e.g. planned MATH 1B -> transcript MATH F001B
            carried = {c["key"]: orphans[gcl.course_key(c["code"])] for c in todo if gcl.course_key(c["code"]) in orphans}
            rest = [c for c in todo if c["key"] not in carried]
            auto = {**carried, **gcl.suggest_placements(rest, {**saved, **carried}, listing)["placements"]}
            if profile_code:
                save_placements(profile_code, {**full, **auto})
                set_setting(profile_code, "gc_auto_done", sorted(done | {c["key"] for c in todo}))
                saved, auto = {**saved, **auto}, {}
    start = {**saved, **auto}          # what the board shows (auto stays unsaved only when there is no profile yet)

    # ---- where the list came from ----
    if listing:
        through = gcl.valid_through(listing)
        first = gcl.term_from_text(plan_start)
        src = "ASSIST's Cal-GETC list" if listing.get("source") == "assist_api" else "Foothill's official Cal-GETC list"
        st.caption(f"Courses are placed for you from {src} for {listing.get('year') or 'this year'}"
                   f"{' (published ' + listing['generated'] + ')' if listing.get('generated') else ''}. "
                   "Drag a card to change it. Courses your major still needs are listed under the board. "
                   "A course only counts if it was approved when you took it.")
        if list_info.get("note"):
            (st.info if list_info.get("fallback_reason") else st.warning)(list_info["note"])
        if list_info.get("status") == "stale":
            st.warning(list_info.get("message", "Using the last saved copy of the list."))
        if through is not None and first is not None and first > through:
            st.warning(f"This list covers courses taken through {term_label(through)}, and your plan starts later. "
                       "Approvals can change each year, so check the newer list if Foothill has published it "
                       "(paste its link under “Use a different list” below).")
    else:
        why = (list_info or {}).get("message")
        if why:
            st.warning(f"Foothill's official Cal-GETC list is not available ({why}) so nothing was placed for you. "
                       "Place your courses by hand, or try again.")
            if on_reload and st.button("Try loading the list again", key="gcb_reload"):
                on_reload()
                st.rerun()
        st.caption("Drag each course into the Cal-GETC area you plan to use it for. SmoothTransfer counts "
                   "them, but it cannot tell whether a course is approved for an area. Check each course "
                   "against Foothill's official Cal-GETC list, or with a counselor.")

    mode = st.radio("How do you want to place courses?", ["Drag and drop", "Menus"],
                    horizontal=True, key="gcb_mode")
    sig = str(hash(json.dumps([sorted(start.items()), sorted(keys)])))  # a new board whenever the saved state changes

    taken = [c for c in courses if c["state"] != "planned"]      # what the student entered
    planned = [c for c in courses if c["state"] == "planned"]    # what the major still needs

    if mode == "Drag and drop":
        groups = sort_items(build_containers(taken, start), multi_containers=True,
                            direction=DIRECTION, custom_style=STYLE, key=f"gcb_board_{sig}")
        new = read_arrangement(groups, taken)
    else:
        options = [NOT_PLACED] + [a["id"] for a in CAL_GETC_AREAS]
        df = pd.DataFrame({
            "Course": [c["code"] for c in taken], "Title": [c["title"] for c in taken],
            "Units": [c["units"] for c in taken],
            "Status": ["in progress" if c["state"] == "in_progress" else "done" for c in taken],
            "Cal-GETC area": [start.get(c["key"], NOT_PLACED) for c in taken],
        }, index=[c["key"] for c in taken])
        edited = st.data_editor(
            df, key=f"gcb_menu_{sig}", hide_index=True,
            disabled=["Course", "Title", "Units", "Status"],
            column_config={"Cal-GETC area": st.column_config.SelectboxColumn(
                "Cal-GETC area", options=options, required=True)})
        new = {k: a for k, a in edited["Cal-GETC area"].items() if a != NOT_PLACED}

    # ---- courses the major still needs: a plain list under the board, not cards ----
    if planned:
        st.markdown("**Can be used from your major:**")
        st.caption("These come from your ASSIST requirements, not from your transcript, so they are not on the "
                   "board. Each one can also count for a Cal-GETC area, and then it is not added to your plan "
                   "a second time. Change the area, or choose “Not used for Cal-GETC”.")
        for c in planned:
            offered = [a for a in gcl.areas_for(listing, c["code"], "")["areas"] if a != "5C"] or ["5C"]
            now = start.get(c["key"], NOT_PLACED)
            opts = [NOT_PLACED] + offered + ([now] if now not in [NOT_PLACED] + offered else [])
            l1, l2, l3 = st.columns([4, 1, 2])
            l1.write(f"**{c['code']}**" + (f" · {c['title']}" if c["title"] else ""))
            l2.write(f"{c['units']:g} units" if c["units"] else "")
            pick = l3.selectbox(f"Cal-GETC area for {c['code']}", opts, index=opts.index(now),
                                key=f"gcb_plan_{c['key']}_{now}", label_visibility="collapsed",
                                format_func=lambda a: "Not used for Cal-GETC" if a == NOT_PLACED else f"Area {a}")
            if pick != NOT_PLACED:
                new[c["key"]] = pick

    st.markdown("**Cal-GETC Requirements Still Needed:**")
    lab = gcl.lab_keys(courses, new, listing) if listing else set()
    rows = summarize(new, courses, lab)
    st.dataframe(pd.DataFrame([{
        "Area": f"{r['id']} · {r['name']}", "Usually needed": r["needed"], "Taken": r["taken"],
        "Planned": r["planned"], "Units": r["units"], "Courses": ", ".join(r["courses"]),
        "Count": area_status(r),
    } for r in rows]), hide_index=True)
    if all(r["enough_taken"] for r in rows):
        st.success("Every area has the usual number of courses placed from courses you have taken or are taking. "
                   "That is a count only. Confirm each course is approved for its area before you rely on it.")
    elif all(r["enough"] for r in rows):
        st.info("With the courses already in your plan, every area will have the usual number. "
                "“Taken” counts courses you have finished or are taking, and “Planned” counts courses you have not taken yet.")
    if any(r["in_progress"] for r in rows):
        st.caption("“Taken” includes courses still in progress, so they only count if you pass them.")
    if any(r["planned"] for r in rows):
        st.caption("Planned courses count once you take and pass them. They are already in your plan, so they "
                   "are not added again as placeholders.")
    if info["blocked"]:
        st.caption("Not placeable: " + "; ".join(f"{c} ({why})" for c, why in info["blocked"])
                   + ". Cal-GETC needs a C or better.")

    # ---- every placement checked against the list ----
    if listing:
        checks = check_placements(courses, new, listing)
        bad = [r for r in checks if not r["ok"]]
        if bad:
            st.warning("Not on Foothill's official list for that area: "
                       + "; ".join(f"{r['course']} in Area {r['area']} ({r['says']})" for r in bad)
                       + ". Move the card, or check with a counselor.")
        with st.expander("Where each placement comes from"):
            if checks:
                st.dataframe(pd.DataFrame([{"Course": r["course"], "Area": r["area"],
                                            "Foothill's list": r["says"]} for r in checks]), hide_index=True)
            left = why_unplaced(courses, new, listing)
            if left:
                st.markdown("**Not placed**")
                st.dataframe(pd.DataFrame([{"Course": k, "Why": v} for k, v in left.items()]), hide_index=True)
            if profile_code and st.button("Redo the placements from the official list (replaces my moves)",
                                          key="gcb_redo"):
                save_placements(profile_code, {k: a for k, a in full.items() if k not in keys})
                set_setting(profile_code, "gc_auto_done", sorted(done - keys))
                st.rerun()

    if new != saved:
        if st.button("Save my placements", type="primary", key="gcb_save"):
            pid = ensure_profile()
            save_placements(pid, {**{k: a for k, a in full.items() if k not in keys}, **new})
            set_setting(pid, "gc_auto_done", sorted(done | keys))   # what the student saved is theirs, not redone
            st.rerun()
    elif saved:
        st.caption("Your placements are saved.")

    return {"saved": saved, "live": new, "dirty": new != saved, "courses": courses, "list_ok": bool(listing)}