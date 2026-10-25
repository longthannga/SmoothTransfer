"""SmoothTransfer web page. Run with:  streamlit run app.py"""
import logging
import os

import streamlit as st
import pandas as pd

logging.getLogger("pypdf").setLevel(logging.ERROR)   # some PDFs have small internal glitches; the text still reads fine

from assist import fetch_majors, fetch_agreement, fetch_institutions
from store import (save_profile, load_profile, cache_majors, get_majors, cache_agreement,
                   get_agreement, add_course, list_courses, delete_course, list_exam_credits)
from transcript_parser import extract_pdf_text
from transcript_match import classify_saved_courses
from cal_getc import CAL_GETC_AREAS, CAL_GETC_NOTE
import requirements_parser
from requirements_parser import parse_requirements, group_sections
from planner import build_plan, adjust_plan, mark_quarters_passed, not_schedulable, needed_courses
import calgetc_api
import calgetc_list
import gc_suggest
import transferable
import institutions
import termwords
from board import render_board
from adjust_guard import conflicting_codes, implies_finished, scheduled_terms_mentioned
from ap import apply_ap, ap_overlaps, ap_signature
from ap_ui import ap_context, render_ap_panel
from cal_getc_board import (render_cal_getc_board, missing_slots, placeholder_reqs, PLACEHOLDER_UNITS,
                            apply_names, units_so_far, area_header, load_placements, save_placements, placeable, unit_plan)
from settings import get_setting, set_setting
from workload import HOURS_PER_UNIT, units_for_codes
from terms import shift_year

import branding
st.set_page_config(page_title="SmoothTransfer", page_icon=branding.page_icon(), layout="wide")
branding.render_header()   # the logo file if it is there, else the 🎓 title
st.caption("Live transfer requirements from ASSIST, and a plan that changes when life does.")

# The pair to plan for. The defaults are Foothill (51) to UC San Diego (7), 2026-27 (77), the only pair tested against live
# ASSIST. To try another, set SENDING_ID, RECEIVING_ID and YEAR_ID in .env (the numbers are in assist.org's web address),
# and optionally SENDING_NAME and RECEIVING_NAME.
SENDING_ID = int(os.getenv("SENDING_ID", "51"))
RECEIVING_ID = int(os.getenv("RECEIVING_ID", "7"))
YEAR_ID = int(os.getenv("YEAR_ID", "77"))
SENDING_NAME = os.getenv("SENDING_NAME") or ("Foothill College" if SENDING_ID == 51 else f"Community college {SENDING_ID}")
CAMPUS = os.getenv("RECEIVING_NAME") or ("UC San Diego" if RECEIVING_ID == 7 else f"University {RECEIVING_ID}")
PAIR_KEY = f"{SENDING_ID}:{RECEIVING_ID}:{YEAR_ID}"

SYS = "quarter"          # "quarter" or "semester": the sending college's term system, set once its agreement is loaded
RECEIVING_KIND = "UC"    # "UC", "CSU" or "": what kind of university the agreement is for


COLLEGE, COLLEGE_FULL = "Foothill", "Foothill College"     # the student's community college, short and full name


def T(text):
    """Put the student's term system and college on screen text. A Foothill student gets the text unchanged."""
    return termwords.T(text, SYS, COLLEGE, COLLEGE_FULL)


def current_college():
    """(short, full) name of the sending college: from the loaded agreement, else the default."""
    info = st.session_state.get("agreement_info")
    full = ((info or {}).get("sending") or {}).get("name") or SENDING_NAME
    return termwords.short_name(full), full


def current_system():
    info = st.session_state.get("agreement_info")
    return (institutions.system_of(info) if info else None) or "quarter"


def current_receiving_kind():
    info = st.session_state.get("agreement_info")
    return (institutions.kind_of(info["receiving"]) if info else "") or "UC"   # UC unless the agreement says CSU
STATUS_LABEL = {"articulated": "Foothill course available", "none": "No Course Articulated",
                "after_transfer": "Take after transfer"}
BADGE = {"light": "🟢 light", "moderate": "🟡 moderate", "heavy": "🔴 heavy"}
MESSAGES = {
    "slow": "ASSIST did not answer, even after a few tries. Wait about a minute and press the button again (agreements you already loaded are saved and open instantly).",
    "blocked": "ASSIST refused the request (code {code}). Please try again.",
    "empty": "ASSIST has no agreement for this choice. Try another major, or check assist.org.",
    "error": "Something went wrong while loading: {detail}",
}

for k in ("majors", "reqs", "major_label", "res", "completed_now", "max_units_now", "goal_now", "profile_code", "major_key",
          "agreement_info"):
    st.session_state.setdefault(k, None)
st.session_state.setdefault("completed_input", [])
st.session_state.setdefault("max_units_input", 16)
if not isinstance(st.session_state.max_units_input, int) or not 8 <= st.session_state.max_units_input <= 20:
    st.session_state.max_units_input = 16
st.session_state.setdefault("start_term_input", "Winter 2027")
st.session_state.setdefault("goal_term_input", "Fall 2028")
st.session_state.setdefault("notes_input", "")
st.session_state.setdefault("upload_nonce", 0)
st.session_state.setdefault("gc_include", True)  # include the missing Cal-GETC courses in the plan
st.session_state.setdefault("sending_pick", SENDING_ID)      # the community college
st.session_state.setdefault("receiving_pick", RECEIVING_ID)   # the university
st.session_state.setdefault("allow_summer_input", False)  # plan summer quarters too?
for _flag, _key in (("set_allow_summer", "allow_summer_input"), ("set_min_units", "min_units_input"),
                    ("set_max_units", "max_units_input"), ("set_goal_term", "goal_term_input")):
    if _flag in st.session_state:  # left by a re-plan, so the plan settings show the values it used
        st.session_state[_key] = st.session_state.pop(_flag)
st.session_state.setdefault("min_units_input", 0)     # minimum units per quarter (0 = no minimum)
st.session_state.setdefault("placeholder_names", {})  # the student's own names for Cal-GETC placeholder courses
st.session_state.setdefault("placeholder_units", {})  # real units of courses picked from the official list


def explain(out):
    text = MESSAGES.get(out["status"], "Unknown problem.")
    st.warning(text.format(code=out.get("code", ""), detail=out.get("detail", "")))


def set_through(term):
    st.session_state["through_term"] = term


def apply_transcript(codes):
    """Button callback: add matched transcript courses to the completed list."""
    st.session_state["completed_input"] = sorted(set(st.session_state.completed_input or []) | set(codes))


def save_current_profile():
    """Save everything the student has on screen into their profile (creating it if needed)."""
    ss = st.session_state
    ss.profile_code = save_profile(
        ss.profile_code, major_key=ss.major_key, major_label=ss.major_label,
        completed=list(ss.completed_input or []), max_units=ss.max_units_input or 16,
        start_term=ss.start_term_input or "Winter 2027", goal_term=ss.goal_term_input or "Fall 2028",
        notes=ss.notes_input or "", plan_res=ss.res)
    persist_settings()
    return ss.profile_code


def persist_settings():
    """Remember the plan choices (include Cal-GETC, minimum units) with the profile, when there is one."""
    code = st.session_state.get("profile_code")
    if code:
        set_setting(code, "include_cal_getc", bool(st.session_state.get("gc_include", True)))
        set_setting(code, "min_units", int(st.session_state.get("min_units_input") or 0))
        set_setting(code, "allow_summer", bool(st.session_state.get("allow_summer_input")))
        set_setting(code, "calgetc_url", (st.session_state.get("gcl_url_input") or "").strip())


def inputs_signature(reqs):
    """What the plan was built from besides the courses: AP coverage and the Cal-GETC placeholders.
    When it changes, the plan is out of date."""
    return (ap_signature(reqs), tuple(sorted(r["course"] for r in reqs if r.get("placeholder"))))


def hours_text(q):
    """' · about 52 hours a week (up to 64 if ...)' for a quarter, using the 4-hours-per-unit rule."""
    h = q.get("weekly_hours")
    if h is None:
        return ""
    text = f" · about {h:g} hours a week"
    if q.get("weekly_hours_max", h) > h:
        text += f" (up to {q['weekly_hours_max']:g} if your Cal-GETC courses are 6 units)"
    return text


def ensure_profile():
    """Return the open profile's code. If there is none yet, create one first, so the student
    never has to set up a profile before saving a course, an exam score, or a plan."""
    if not st.session_state.profile_code:
        save_current_profile()
        st.session_state["profile_notice"] = (
            "A new profile was created so your records are saved. Keep this code to come back "
            f"later: `{st.session_state.profile_code}`")
    return st.session_state.profile_code


# Widgets remember what was typed in them. These are the ones that hold one student's records,
# so they must be cleared when a profile is closed or another one is opened.
WIDGET_PREFIXES = ("gcl_", "opt_", "pending_transcript", "change_note", "earlier-fail-", "ph_", "apw_", "gcb_", "cal_getc_", "result-", "confirm-", "future-", "transcript_",
                   "count_in_progress", "remove_course_id", "through_term", "reset_through")
KEEP_ON_EXIT = {"majors", "majors_pair", "institutions_raw", "upload_nonce"}  # public ASSIST data, and the uploader reset counter


def forget_widget_state():
    for k in list(st.session_state.keys()):
        if k.startswith(WIDGET_PREFIXES):
            del st.session_state[k]
    st.session_state["upload_nonce"] = st.session_state.get("upload_nonce", 0) + 1


def exit_profile():
    """Button callback: save the open profile, then clear everything personal from this screen."""
    code = st.session_state.get("profile_code")
    notice = None
    if code:
        try:
            save_current_profile()  # keep edits that were not saved yet
            notice = (f"Profile closed. Your code is `{code}`. "
                      "Enter it under “Saved profile” to open it again.")
        except Exception as e:
            notice = f"Profile closed, but the latest changes could not be saved: {str(e)[:120]}"
    nonce = st.session_state.get("upload_nonce", 0) + 1
    for k in list(st.session_state.keys()):
        if k not in KEEP_ON_EXIT:
            del st.session_state[k]
    st.session_state["upload_nonce"] = nonce
    st.session_state["exit_notice"] = notice or "Nothing was open. Your screen is cleared."


_pending = st.session_state.pop("pending_completed", None)
if _pending:
    apply_transcript(_pending)


def options_text(r):
    return " OR ".join(" + ".join(c["code"] for c in g) for g in r["options"]) or (r["note"] or "—")


def render_item(item):
    if isinstance(item, dict):
        course = item.get("course", "")
        note = item.get("note", "")
        return f"**{course}**: {note}" if course else note
    return str(item)


def _loaded_chart():
    """The AP chart if loaded, otherwise the IB chart (used for the 'credit is awarded only once' note)."""
    infos = st.session_state.get("ap_chart_infos") or {}
    return (infos.get("ap") or infos.get("ib") or {}).get("chart")


def render_name_editor(res):
    """Pick the real course for a Cal-GETC placeholder card. With Foothill's official list loaded, the student picks
    from the approved courses for that area and the plan then uses the course's real units, not a 5-unit guess.
    Without the list, or for a course that is not on it, they type the name and the units stay a 4-6 estimate."""
    plan = res["plan"]
    cards = [c for q in plan["quarters"] for c in q["courses"] if c.get("placeholder")]
    if not cards:
        return
    ss = st.session_state
    names, units = ss.get("placeholder_names") or {}, ss.get("placeholder_units") or {}
    generic = {c["code"]: (c.get("generic_title") or c["title"]) for c in cards}
    area_of = {c["code"]: (c.get("area_id") or c["code"].split()[1]) for c in cards}
    if ss.get("ph_pick") not in generic:
        ss["ph_pick"] = next(iter(generic))
    if ss.pop("reset_ph_name", False):
        ss["ph_new_name"] = ""
    listing = (ss.get("gcl_info") or {}).get("listing")
    # Claude suggests one approved course per card (checked against the official list afterwards)
    if listing:
        todo = [c for c in cards if not names.get(c["code"])]
        if todo and st.button("Suggest a course for each empty Cal-GETC card", key="gcs_go"):
            with st.spinner("Choosing from the official list..."):
                _used = set(ss.get("gc_used_keys") or set()) | {calgetc_list.course_key(v) for v in names.values() if v}
                _ctx = {"major": ss.get("major_label"), "campus": CAMPUS, "notes": ss.get("notes_input", ""),
                        "major_courses": sorted({c["code"] for r in (ss.get("reqs") or []) for g in r["options"] for c in g})}
                ss["gcs_result"] = gc_suggest.suggest(todo, listing, _ctx, used_keys=_used)
        res_s = ss.get("gcs_result")
        if res_s:
            if res_s.get("error"):
                st.warning(res_s["error"])
            elif res_s["picks"]:
                st.dataframe(pd.DataFrame([{"Card": generic.get(k, k), "Suggested course": f"{v['course']} · {v['title']}",
                                            "Units": v["units"], "Why": v["why"]} for k, v in res_s["picks"].items()]),
                             hide_index=True)
                st.caption("Claude chose these only from the official list, and the app checked each one. "
                           "Confirm with a counselor, and change any you do not like with the picker below.")
                if st.button("Use these suggestions", key="gcs_apply", type="primary"):
                    merged_n, merged_u = dict(names), dict(units)
                    for k, v in res_s["picks"].items():
                        if not merged_n.get(k):
                            merged_n[k], merged_u[k] = v["course"], v["units"]
                    pid = ensure_profile()
                    set_setting(pid, "placeholder_names", merged_n)
                    set_setting(pid, "placeholder_units", merged_u)
                    ss["placeholder_names"], ss["placeholder_units"] = merged_n, merged_u
                    apply_names(plan, merged_n, merged_u, system=SYS)
                    save_current_profile()
                    ss["gcs_result"] = None
                    st.rerun()
            if res_s.get("dropped"):
                st.caption("Left out: " + "; ".join(res_s["dropped"][:4]))
    st.markdown("**Picked a real course for a Cal-GETC card? Choose it here.**")
    r1, r2 = st.columns([3, 2])
    pick = r1.selectbox("Card", list(generic), key="ph_pick",
                        format_func=lambda k: generic[k] + (f"  →  {names[k]}" + (f" ({units[k]:g} units)" if units.get(k) else "")
                                                            if names.get(k) else ""))
    used = set(ss.get("gc_used_keys") or set()) | {calgetc_list.course_key(v) for k, v in names.items() if k != pick and v}
    approved = calgetc_list.courses_for_area(listing, area_of[pick], used) if listing else []
    choice = "—"
    if approved:
        labels = {c["code"]: f"{c['code']} · {c['title']} · {c['units']:g} units"
                              + (" · with lab" if c["lab"] and area_of[pick] in ("5A", "5B") else "")
                              + (f" · approval ends {c['removed']}" if c["removed"] else "") for c in approved}
        choice = r1.selectbox(T("Pick from Foothill's approved list (uses the real units)"), ["—"] + list(labels),
                              key=f"ph_choice_{pick}",
                              format_func=lambda c: "Choose a course..." if c == "—" else labels[c])
    elif not listing:
        _pu, (_lo, _hi) = unit_plan(SYS)
        r1.caption(T(f"Foothill's list is not loaded, so type the course name. It still counts as an estimate of {_lo}-{_hi} units."))
    new_name = r2.text_input("Or type a course that is not on the list", key="ph_new_name", placeholder="e.g. ART F001")
    if st.button("Use this course", key="ph_rename"):
        merged_n, merged_u = dict(names), dict(units)
        if choice != "—":
            c = next(x for x in approved if x["code"] == choice)
            merged_n[pick], merged_u[pick] = c["code"], c["units"]
        elif new_name.strip():
            merged_n[pick] = new_name.strip()
            merged_u.pop(pick, None)
        else:
            merged_n.pop(pick, None)
            merged_u.pop(pick, None)
        pid = ensure_profile()
        set_setting(pid, "placeholder_names", merged_n)
        set_setting(pid, "placeholder_units", merged_u)
        ss["placeholder_names"], ss["placeholder_units"] = merged_n, merged_u
        apply_names(plan, merged_n, merged_u, system=SYS)
        save_current_profile()  # saves the updated plan with the profile
        ss["reset_ph_name"] = True
        st.rerun()
    # two picks that break a Cal-GETC rule
    picks = {k: v for k, v in names.items() if v and k in generic}
    same = [v for v in {v for v in picks.values()} if sum(1 for x in picks.values() if x == v) > 1]
    if same:
        st.warning("The same course is picked for more than one card: " + ", ".join(same) + ". A course can count for one area only.")
    by_disc = {}
    for k, v in picks.items():
        if area_of.get(k) == "4":
            by_disc.setdefault(calgetc_list.discipline(v), []).append(v)
    for d, vs in by_disc.items():
        if len(vs) > 1:
            st.warning(f"Area 4 needs courses from at least two different disciplines, but {', '.join(vs)} are both {d}.")


def apply_plan_change(plan_now, reqs, new_completed, change, new_max, new_min, *, past_terms=(), failed=(),
                      only_passed=False, goal=None, allow_summer=None, work_hours=None):
    """Re-plan (or just mark quarters as passed), save everything with the profile, and refresh the page.
    Used by the Adjust button and by each option in the options panel."""
    ss = st.session_state
    goal = ss.get("goal_now") or "" if goal is None else goal
    summer = bool(ss.get("allow_summer_now", False)) if allow_summer is None else allow_summer
    with st.spinner("Updating your plan..."):
        try:
            if only_passed:  # everything passed, nothing else changed: no Claude call
                ss.res = mark_quarters_passed(plan_now, reqs, new_completed, list(past_terms), new_max, new_min)
            else:
                ss.res = adjust_plan(plan_now, reqs, new_completed, change.strip() or "Some quarters are finished.",
                                     new_max, goal, list(past_terms), set(failed), min_units=new_min,
                                     allow_summer=summer, work_hours=work_hours, term_system=SYS)
            ss.completed_now = new_completed
            ss.max_units_now = new_max
            ss.min_units_now = new_min
            ss.goal_now = goal
            ss.allow_summer_now = summer
            ss["set_allow_summer"], ss["set_min_units"] = summer, int(new_min)
            ss["set_max_units"], ss["set_goal_term"] = int(new_max), goal
            if ss.profile_code:
                ss.profile_code = save_profile(
                    ss.profile_code, major_key=ss.major_key, major_label=ss.major_label,
                    completed=new_completed, max_units=new_max, start_term=ss.get("start_term_input", "Winter 2027"),
                    goal_term=goal or "Fall 2028", notes=ss.get("notes_input", ""), plan_res=ss.res)
                set_setting(ss.profile_code, "min_units", int(new_min))
                set_setting(ss.profile_code, "allow_summer", bool(summer))
            ss["reset_through"] = True
            ss["reset_change"] = True
            st.rerun()
        except Exception as e:
            st.error(f"Could not update the plan: {str(e)[:200]}. Please try again.")


def plan_is_strained(res):
    """True when the plan has problems left or a quarter Claude called heavy."""
    return bool(res.get("issues_remaining")) or any(q.get("workload") == "heavy" for q in res["plan"]["quarters"])


def render_options(res, reqs):
    """The ways to fix a heavy or tight plan, side by side. The student chooses; nothing is picked for them."""
    ss = st.session_state
    base = dict(plan_now=res["plan"], reqs=reqs, new_completed=list(ss.completed_now or []),
                new_max=int(ss.max_units_now or 16), new_min=int(ss.get("min_units_now") or 0))
    goal = ss.get("goal_now") or ""
    later = shift_year(goal, 1)
    with st.expander("Your options if this plan is too heavy or too tight", expanded=plan_is_strained(res)):
        st.caption("There is more than one way to fix a heavy plan, and the right one depends on your life. "
                   "Pick the one you want. The plan is re-made with it, and you can try another afterwards.")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Work fewer hours**")
            st.caption("Same courses, same dates. More of your week goes to school.")
            hours = st.number_input("Hours a week I can work", 0, 60, 20, key="opt_work_hours")
            if st.button("Re-plan with these work hours", key="opt_work"):
                apply_plan_change(change=f"I can work {hours} hours a week from now on.", work_hours=int(hours), **base)
        with c2:
            st.markdown(T("**Add summer quarters**"))
            st.caption(T("The same courses spread over more quarters, so each quarter is lighter."))
            if ss.get("allow_summer_now"):
                st.success(T("Summer quarters are already allowed."))
            elif st.button(T("Allow summer quarters"), key="opt_summer"):
                apply_plan_change(change="I am willing to take summer quarters.", allow_summer=True, **base)
        with c3:
            st.markdown("**Move my transfer a year later**")
            st.caption(T("A later transfer date gives you more quarters to finish in."))
            if later:
                if st.button(f"Move my transfer to {later}", key="opt_later"):
                    apply_plan_change(change=f"I am moving my transfer goal from {goal} to {later}.", goal=later, **base)
            else:
                st.caption("Write the transfer goal like “Fall 2028” to use this option.")


def render_plan(res, reqs, completed, max_units, min_units=0, units_before=0.0, units_note=""):
    plan = res["plan"]
    st.info(plan["summary"])

    if res["issues_caught"]:
        st.success(f"The built-in checker caught {len(res['issues_caught'])} problem(s) in the first "
                   "draft, and the plan was fixed before you saw it.")
        with st.expander("What the checker caught"):
            for p in res["issues_caught"]:
                st.write("•", p)
    if res["issues_remaining"]:
        st.error("The checker still sees problems. Check this plan carefully with a counselor.")
        for p in res["issues_remaining"]:
            st.write("•", p)

    for w in ap_overlaps(reqs, completed, plan.get("quarters", []),
                         st.session_state.get("ap_derived") or (), _loaded_chart()):
        st.warning(w)

    if plan.get("what_changed"):
        st.subheader("What changed")
        for line in plan["what_changed"]:
            st.write("•", line)

    if plan.get("past_quarters"):
        st.subheader(T("Finished quarters"))
        for q in plan["past_quarters"]:
            done = "  ·  ".join(
                ("✅ " if c.get("result") == "passed" else "❌ ") + (c.get("title") or c["code"])
                for c in q["courses"])
            st.markdown(f"**{q['term']}**: {done or 'No classes'}")

    st.subheader(T("Quarter by quarter"))
    st.caption(T("Drag a course card into another quarter to try a different order."))
    if any(c.get("placeholder") for q in plan["quarters"] for c in q["courses"]):
        _pu, (_lo, _hi) = unit_plan(SYS)
        st.caption("Cal-GETC cards such as “1 arts course” are placeholders. Pick a real course for that area "
                   f"with the picker under the plan, and its real units replace the {_pu}-unit estimate "
                   f"(a placeholder course may really be {_lo} to {_hi}).")
    if render_board(res, reqs, completed, max_units, min_units, units_before, units_note, system=SYS):
        st.rerun()
    render_name_editor(res)

    with st.expander(T("Why each course is in its quarter")):
        for q in plan["quarters"]:
            st.markdown(f"**{q['term']}** · {q['total_units']} units · "
                        f"{BADGE.get(q['workload'], q['workload'])}{hours_text(q)}")
            if q["courses"]:
                st.table([{
                    "Course": c.get("title") or c["code"],
                    "Satisfies": c["satisfies"],
                    "Units": str(c.get("units_label") or f"{c['units']:g}"),   # always text, so the column has one type
                    "Why": c["why"],
                    "Verify prerequisite": "yes" if c.get("prereq_assumed") else "",
                } for c in q["courses"]])
            else:
                st.caption(T("No classes. Open quarter."))
            st.caption(q["workload_note"])

    for key, title in (("cannot_do_at_foothill", T("Cannot be done at Foothill")),
                       ("needs_check", "Needs a counselor check"),
                       ("warnings", "Warnings and backup plan")):
        if plan.get(key):
            with st.expander(title, expanded=(key == "warnings")):
                for item in plan[key]:
                    st.markdown("• " + render_item(item))


def save_transcript_rows(rows, school, all_codes, parser_used="simple"):
    """Save reviewed transcript rows to the open profile (creating one if needed) and tick the matching
    completed courses. Returns (rows saved, courses matched to this major)."""
    ensure_profile()
    saved = 0
    for row in rows:
        code = str(row.get("course_code", "")).strip()
        if not code:
            continue
        try:
            add_course(st.session_state.profile_code, course_code=code,
                       title=str(row.get("title", "")), institution=school, term=str(row.get("term", "")),
                       units=float(row.get("units") or 0) or None, status="Needs review",
                       source="transcript_pdf", notes=(f"Extracted grade: {row.get('grade', '')}; "
                                                       f"record_type: {row.get('record_type', 'course')}; "
                                                       f"parser: {row.get('parser_source', parser_used)}"))
            saved += 1
        except Exception as e:
            st.warning(f"Skipped {code}: {e}")
    matched = classify_saved_courses(list_courses(st.session_state.profile_code), all_codes)["completed"]
    # Applied at the top of the next run: the completed-courses box may already exist in this run, and
    # Streamlit does not allow changing a box's value once it is drawn.
    st.session_state["pending_completed"] = list(matched)
    st.session_state.pop("pending_transcript", None)
    msg = (f"Saved {saved} course record(s) for review. {len(matched)} matched this major's requirements "
           "and were added to your completed list.")
    st.session_state["profile_notice"] = (st.session_state.get("profile_notice", "") + " " + msg).strip()
    return saved, len(matched)


def render_transcript_upload(all_codes, sfx=""):
    """Upload a transcript PDF, review the rows, and save them to the open profile. The rows are also kept as
    st.session_state["pending_transcript"], so the Cal-GETC board can offer to use them without a second upload."""
    transcript = st.file_uploader("Choose a text-based transcript PDF", type=["pdf"], key=f"transcript_pdf{sfx}_{st.session_state.upload_nonce}")
    if transcript is None:
        st.session_state.pop("pending_transcript", None)
        return
    try:
        extracted = extract_pdf_text(transcript.getvalue())
        st.info(extracted["message"])
        if extracted["needs_ocr"]:
            st.session_state.pop("pending_transcript", None)
            st.warning("This appears to be a scanned PDF. OCR is not included yet; add courses manually for now.")
        elif extracted["courses"]:
            st.caption(f"Parser: {extracted.get('parser_used', 'simple')}. Review and correct every row. Nothing is saved until you click the save button.")
            edited = st.data_editor(pd.DataFrame(extracted["courses"]), num_rows="dynamic",
                                    width="stretch", key=f"transcript_review{sfx}_{transcript.name}")
            transcript_school = st.text_input("Institution for these courses", key=f"transcript_school{sfx}")
            rows = edited.to_dict(orient="records")
            st.session_state["pending_transcript"] = {"name": transcript.name, "rows": rows, "school": transcript_school,
                                                      "parser": extracted.get("parser_used", "simple")}
            if st.button("Save reviewed transcript courses", key=f"save_transcript_courses{sfx}"):
                save_transcript_rows(rows, transcript_school, all_codes, extracted.get("parser_used", "simple"))
                st.rerun()
        else:
            st.session_state.pop("pending_transcript", None)
            st.warning(extracted["message"])
    except Exception as e:
        st.session_state.pop("pending_transcript", None)
        st.error(f"Could not read this PDF: {e}")


# ---------- Open profile bar ----------
# The banner always has a slot, even when it is empty. Otherwise it appearing and disappearing shifts every
# element below it, and Streamlit then forgets which boxes the student had open.
_banner = st.empty()
_texts = [m for m in (st.session_state.pop(k, None) for k in ("exit_notice", "profile_notice")) if m]
if _texts:
    _banner.success(" ".join(_texts))
if st.session_state.profile_code:
    bar1, bar2 = st.columns([5, 1])
    bar1.info(f"Profile open: `{st.session_state.profile_code}`. Keep this code to come back later.")
    bar2.button("Exit profile", on_click=exit_profile, key="exit_profile")

# ---------- Saved profile ----------
with st.expander("Saved profile", expanded=not bool(st.session_state.profile_code)):
    st.caption("Profiles are stored in this app's SQLite database. The profile code acts like a private link; anyone who has it can open the profile.")
    p1, p2 = st.columns([3, 1])
    profile_code_entry = p1.text_input("Profile code", value=st.session_state.profile_code or "", key="profile_code_entry")
    if p2.button("Load profile"):
        profile = load_profile(profile_code_entry)
        if not profile:
            st.error("No profile found for that code.")
        else:
            forget_widget_state()
            st.session_state.profile_code = profile["code"]
            st.session_state["gc_include"] = bool(get_setting(profile["code"], "include_cal_getc", True))
            st.session_state["min_units_input"] = int(get_setting(profile["code"], "min_units", 0) or 0)
            st.session_state["allow_summer_input"] = bool(get_setting(profile["code"], "allow_summer", False))
            st.session_state["gcl_url_input"] = get_setting(profile["code"], "calgetc_url", "") or ""
            st.session_state["placeholder_names"] = get_setting(profile["code"], "placeholder_names", {}) or {}
            st.session_state["placeholder_units"] = get_setting(profile["code"], "placeholder_units", {}) or {}
            st.session_state.major_key = profile.get("major_key")
            _ids = institutions.ids_from_key(profile.get("major_key"))
            if _ids:
                st.session_state["sending_pick"], st.session_state["receiving_pick"] = _ids["sending"], _ids["receiving"]
            st.session_state.major_label = profile.get("major_label")
            st.session_state.completed_input = profile.get("completed", [])
            st.session_state.completed_now = profile.get("completed", [])
            st.session_state.max_units_input = profile.get("max_units") or 16
            st.session_state.max_units_now = profile.get("max_units") or 16
            st.session_state.start_term_input = profile.get("start_term") or "Winter 2027"
            st.session_state.goal_term_input = profile.get("goal_term") or "Fall 2028"
            st.session_state.goal_now = profile.get("goal_term") or "Fall 2028"
            st.session_state.notes_input = profile.get("notes") or ""
            st.session_state.res = profile.get("plan")
            key = profile.get("major_key")
            if key:
                agreement, _ = get_agreement(key)
                if agreement is None:
                    out = fetch_agreement(key)
                    if out["status"] == "ok":
                        agreement = out["data"]
                        cache_agreement(key, agreement)
                if agreement is not None:
                    st.session_state.reqs = parse_requirements(agreement)
                    st.session_state["parse_notes"] = list(requirements_parser.PARSE_NOTES)
                    st.session_state["agreement_info"] = institutions.institution_info(agreement)
            st.rerun()
    if st.session_state.profile_code:
        st.caption(f"Current profile: `{st.session_state.profile_code}`")
    else:
        st.markdown("**No profile yet?** One is created for you the first time you save a course, "
                    "an exam score, or a plan. You can also create it now.")
        if st.button("Create profile for my academic records", key="create_records_profile"):
            st.session_state.profile_code = save_profile(
                major_key=st.session_state.major_key,
                major_label=st.session_state.major_label,
                completed=st.session_state.completed_input or [],
                max_units=st.session_state.max_units_input or 16,
                start_term=st.session_state.start_term_input or "Winter 2027",
                goal_term=st.session_state.goal_term_input or "Fall 2028",
                notes=st.session_state.notes_input or "",
                plan_res=None,
            )
            st.success(f"Academic-record profile created. Save this profile code: {st.session_state.profile_code}")
            st.rerun()

# ---------- 1. Target ----------
st.header("1. Your target")


def institution_list():
    """Every college and university from ASSIST once loaded (and cached for a month). Before that, just Foothill and
    UC San Diego, so the page works straight away."""
    raw = st.session_state.get("institutions_raw")
    if raw is None:
        raw, _ = get_majors("institutions", 30)
        if raw:
            st.session_state["institutions_raw"] = raw
    return institutions.parse_institutions(raw) if raw else institutions.FALLBACK


def _with(options, picked, fallback_name):
    """Make sure the current pick is one of the options, so the box never fails on a saved or configured school."""
    if picked in [o["id"] for o in options]:
        return options
    return options + [{"id": picked, "name": fallback_name, "terms": [], "term_type": None}]


_insts = institution_list()
_ccs = _with(institutions.sending_options(_insts), int(st.session_state.sending_pick), SENDING_NAME)
_unis = _with(institutions.receiving_options(_insts), int(st.session_state.receiving_pick), CAMPUS)
_by_id = {i["id"]: i for i in _insts + _ccs + _unis}
c1, c2 = st.columns(2)
c1.selectbox("Community college", [i["id"] for i in _ccs], key="sending_pick",
             format_func=lambda i: institutions.describe(_by_id[i], YEAR_ID))
c2.selectbox("University", [i["id"] for i in _unis], key="receiving_pick",
             format_func=lambda i: institutions.describe(_by_id[i], YEAR_ID))
if st.session_state.get("institutions_raw") is None:
    st.caption(T("Showing Foothill College and UC San Diego. Load ASSIST's full list to choose another college or university."))
    if st.button("Load all colleges and universities from ASSIST", key="load_institutions"):
        with st.spinner("Asking ASSIST for its list of colleges and universities..."):
            _out = fetch_institutions()
        if _out["status"] == "ok":
            st.session_state["institutions_raw"] = _out["data"]
            cache_majors("institutions", _out["data"])
            st.rerun()
        else:
            explain(_out)
_snd, _rcv = int(st.session_state.sending_pick), int(st.session_state.receiving_pick)
_pair_key = f"{_snd}:{_rcv}:{YEAR_ID}"
_pick_system = institutions.system_for_year(_by_id[_snd], institutions.year_start(YEAR_ID)) if _snd in _by_id else None
if _pick_system:
    st.caption(f"{_by_id[_snd]['name']} uses {_pick_system}s, so the plan is in {_pick_system}s and its units.")
_loaded_ids = institutions.ids_from_key(st.session_state.major_key) if st.session_state.major_key else None
if _loaded_ids and (_loaded_ids["sending"], _loaded_ids["receiving"]) != (_snd, _rcv):
    st.warning("The major below was loaded for a different college or university than the ones picked here. "
               "Load the majors for this pair to change it.")

if st.button("Load majors live from ASSIST"):
    with st.spinner("Checking the local cache, then ASSIST if needed..."):
        majors, _ = get_majors(_pair_key)
        out = {"status": "ok", "data": majors} if majors else fetch_majors(_snd, _rcv, YEAR_ID)
    if out["status"] == "ok":
        st.session_state.majors = out["data"]
        st.session_state["majors_pair"] = _pair_key
        cache_majors(_pair_key, out["data"])
    else:
        explain(out)

if st.session_state.majors and st.session_state.get("majors_pair") not in (None, _pair_key):
    st.info("You picked a different college or university. Load its majors to continue.")
elif st.session_state.majors:
    hide_minors = st.checkbox("Hide minors", value=True)
    labels = [m["label"] for m in st.session_state.majors
              if not (hide_minors and "minor" in m["label"].lower())]
    label = st.selectbox("Major", sorted(labels))
    if st.button("Load requirements live"):
        key = next(m["key"] for m in st.session_state.majors if m["label"] == label)
        with st.spinner("Checking the local cache, then ASSIST if needed..."):
            agreement, _ = get_agreement(key)
            out = {"status": "ok", "data": agreement} if agreement is not None else fetch_agreement(key)
        if out["status"] == "ok":
            cache_agreement(key, out["data"])
            st.session_state.reqs = parse_requirements(out["data"])
            st.session_state["parse_notes"] = list(requirements_parser.PARSE_NOTES)
            st.session_state["agreement_info"] = institutions.institution_info(out["data"])
            if institutions.system_of(st.session_state["agreement_info"]) == "semester":
                # a semester college: the quarter-style defaults (a Winter start, 16 units) cannot be right
                if st.session_state.get("start_term_input") in (None, "", "Winter 2027"):
                    st.session_state["start_term_input"] = "Spring 2027"
                if st.session_state.get("max_units_input") in (None, 16):
                    st.session_state["max_units_input"] = 15
            st.session_state.major_label = label
            st.session_state.major_key = key
            st.session_state.res = None
        else:
            explain(out)

# ---------- 2. Requirements ----------
reqs = st.session_state.reqs
ap_ctx = None
SYS = current_system()
RECEIVING_KIND = current_receiving_kind()
COLLEGE, COLLEGE_FULL = current_college()
if reqs:
    ap_ctx = ap_context(st.session_state.major_key, st.session_state.profile_code)
    reqs = apply_ap(reqs, ap_ctx["charts"], ap_ctx["exams"], ap_ctx["allow_unverified"])
    st.header("2. Requirements for " + st.session_state.major_label)
    for _note in st.session_state.get("parse_notes") or []:
        st.warning(_note)
    sections = group_sections(reqs)
    counts = {}
    for r in reqs:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    m1, m2, m3 = st.columns(3)
    m1.metric(T("Requirements to finish at Foothill"), int(sum(g["required"] for g in sections)))
    m2.metric("Covered by AP", sum(g["ap_covered"] for g in sections))
    # needed, but ASSIST has no Foothill course for them ("No Course Articulated" or "after transfer"),
    # not counting optional alternatives in a "pick N" section
    m3.metric("Take at the university after transfer", len(not_schedulable(reqs)))

    for g in sections:
        with st.container(border=True):
            st.markdown(f"**Section {g['label']}** · {g['rule']}")
            if g.get("pick") and g.get("pick_unit") not in (None, "course"):
                st.warning(f"ASSIST counts this section in {g['pick_unit']}, not courses. SmoothTransfer treats each "
                           "listed row as one choice, so if a series means several courses taken together, check "
                           "this section against assist.org before relying on the plan.")
            st.table([{"UC course": r["course"], "Status": T(STATUS_LABEL.get(r["status"], r["status"])),
                       T("Foothill options"): options_text(r),
                       **({"AP credit": ("Covered by " + "; ".join(r["ap_by"])) if r.get("ap_met") else ""}
                          if any(x.get("ap_met") for x in g["items"]) else {})}
                      for r in g["items"]])

    # ---------- 3. Student info ----------
    st.header("3. About you")
    all_codes = sorted({c["code"] for r in reqs for g in r["options"] for c in g})
    # ---------- Academic record ----------
    st.subheader("Your academic record")
    if not st.session_state.profile_code:
        st.caption("A profile is created automatically the first time you save something here.")

    with st.expander("Add a course manually", expanded=False):
        with st.form("manual_course_form", clear_on_submit=True):
            mc1, mc2 = st.columns(2)
            manual_code = mc1.text_input("Course code (e.g. ENGL 1A)")
            manual_title = mc2.text_input("Course title")
            mc3, mc4, mc5 = st.columns(3)
            manual_school = mc3.text_input("College / institution", value=COLLEGE_FULL)
            manual_term = mc4.text_input("Term (optional)")
            manual_units = mc5.number_input("Units", min_value=0.0, max_value=30.0, value=0.0, step=0.5)
            NO_AREA = "Not for Cal-GETC"
            manual_area = st.selectbox("Cal-GETC area (optional)", [NO_AREA] + [area_header(a) for a in CAL_GETC_AREAS],
                                       help="Pick an area to put this course straight onto the Cal-GETC board.")
            manual_status = st.selectbox("Record status", ["Needs review", "Verified", "Unmapped"])
            manual_notes = st.text_input("Notes (optional)")
            add_manual = st.form_submit_button("Add course")
        if add_manual:
            if not manual_code.strip():
                st.error("Enter a course code.")
            else:
                profile_for_course = ensure_profile()
                add_course(profile_for_course, course_code=manual_code, title=manual_title,
                           institution=manual_school, term=manual_term, units=manual_units or None,
                           status=manual_status, notes=manual_notes)
                note = f"Added {manual_code.strip().upper()}."
                if manual_area != NO_AREA:
                    placements = load_placements(profile_for_course)
                    placements[manual_code.strip().upper()] = manual_area.split(" ·")[0]
                    save_placements(profile_for_course, placements)
                    note += f" It is on the Cal-GETC board in area {manual_area.split(' ·')[0]}."
                st.session_state["profile_notice"] = (st.session_state.get("profile_notice", "") + " " + note).strip()
                st.rerun()

    with st.expander("Upload transcript PDF", expanded=False):
        render_transcript_upload(all_codes)

    if st.session_state.profile_code:
        saved_courses = list_courses(st.session_state.profile_code)
        if saved_courses:
            st.markdown("**Saved course records**")
            st.dataframe(pd.DataFrame(saved_courses)[["course_code", "title", "institution", "term", "units", "status", "source"]],
                         width="stretch", hide_index=True)
            remove_id = st.selectbox("Course record to remove", [None] + [c["id"] for c in saved_courses],
                format_func=lambda x: "Select course" if x is None else next(
                    f"{c['course_code']} · {c['institution']} · #{c['id']}" for c in saved_courses if c["id"] == x),
                key="remove_course_id")
            if st.button("Remove selected course", disabled=remove_id is None):
                delete_course(st.session_state.profile_code, remove_id)
                st.rerun()

    if st.session_state.profile_code:
        tm = classify_saved_courses(list_courses(st.session_state.profile_code), all_codes)
        st.session_state["ap_derived"] = {m["assist"] for m in tm["matches"] if m["kind"] == "ap_ib_or_transfer"}
        if tm["matches"]:
            st.markdown("**Transcript courses that match this major**")
            st.caption("Transcript names (CS F003B) are matched to ASSIST names (C S 3B). "
                       "Only courses you passed count as completed.")
            st.dataframe(pd.DataFrame(tm["matches"]).rename(columns={
                "transcript": "On your transcript", "assist": "ASSIST course",
                "grade": "Grade", "kind": "Type"}), width="stretch", hide_index=True)
            count_ip = st.checkbox("Also count courses I am taking now (I expect to pass them)",
                                   key="count_in_progress") if tm["in_progress"] else False
            to_add = tm["completed"] + (tm["in_progress"] if count_ip else [])
            missing = [c for c in to_add if c not in (st.session_state.completed_input or [])]
            if missing:
                st.info(f"{len(missing)} matched course(s) are not in your completed list yet: "
                        + ", ".join(missing))
                st.button("Add them to my completed list", on_click=apply_transcript, args=(missing,))
            else:
                st.success("Every matched course is already in your completed list.")
            if tm["in_progress"] and not count_ip:
                st.caption("In progress, not counted yet: " + ", ".join(tm["in_progress"]))
        if tm["below_c"]:
            st.warning("Grade below C, so not counted automatically: "
                       + ", ".join(f"{c} ({g})" for c, g in tm["below_c"])
                       + ". Ask a counselor whether it still counts for your major.")
        if tm["not_passed"]:
            st.warning("Taken but not passed, so still needed: " + ", ".join(tm["not_passed"]))
        if tm["deanza"]:
            st.info("De Anza courses are not counted. This ASSIST agreement is for Foothill only: "
                    + ", ".join(tm["deanza"]))

    # ---------- AP credit and Cal-GETC come BEFORE the plan, because both change it ----------
    with st.expander("AP / IB exam credit", expanded=False):
        render_ap_panel(ap_ctx, st.session_state.major_key, reqs, ensure_profile)

    st.subheader("Cal-GETC general education")
    st.warning(CAL_GETC_NOTE)
    _records = list_courses(st.session_state.profile_code) if st.session_state.profile_code else []
    # The board is NOT inside an expander on purpose. Tested in a browser: the drag widget sometimes
    # ends up with zero height when it first loads inside a collapsed expander.
    def use_pending_transcript():
        pending = st.session_state.get("pending_transcript")
        if pending:
            save_transcript_rows(pending["rows"], pending["school"], all_codes, pending.get("parser", "simple"))

    # Foothill's official Cal-GETC list: read once per session (and cached for a week), so courses can be
    # placed for the student. If it cannot be loaded the board still works by hand.
    _url = (st.session_state.get("gcl_url_input") or "").strip()
    _force = st.session_state.pop("gcl_force", False)
    _pair = institutions.ids_from_key(st.session_state.major_key) or {"sending": SENDING_ID, "year": YEAR_ID}
    _asked = (_url, _pair["sending"], _pair["year"])
    _info = st.session_state.get("gcl_info")
    if _force or _info is None or _info.get("asked") != _asked:
        with st.spinner("Reading the Cal-GETC course list..."):
            # ASSIST's list for this college and year; Foothill's PDF is the backup, for Foothill only
            _info = calgetc_api.load_best(_url or None, force_live=_force, institution=_pair["sending"], year=_pair["year"])
        _info["asked"] = _asked
        st.session_state["gcl_info"] = _info
    st.caption(f"Cal-GETC list source: ASSIST list for college {_pair['sending']}, year {_pair['year']} "
               f"(status: {_info.get('status')}). The address only works from inside assist.org's own page, "
               "so opening it in a tab shows an error. That is normal.")
    if _info.get("status") not in ("ok", "cached", "stale"):
        st.warning(f"The Cal-GETC list for {COLLEGE} did not load: {_info.get('message') or _info.get('status')}")
        st.button("Try loading the list again", key="gcl_retry_top", on_click=lambda: st.session_state.__setitem__("gcl_force", True))
    _info["college"] = COLLEGE                       # so the board says "Evergreen Valley's list", not Foothill's
    if _info.get("listing"):
        _info["listing"]["college"] = COLLEGE

    def reload_list():
        st.session_state["gcl_force"] = True

    _done_now = [c for c in (st.session_state.completed_input or []) if c in all_codes]
    _gc = render_cal_getc_board(st.session_state.profile_code, _records, ensure_profile,
                                pending=st.session_state.get("pending_transcript"),
                                on_use_pending=use_pending_transcript,
                                needed=needed_courses(reqs, set(_done_now)), list_info=_info,
                                on_reload=reload_list, plan_start=st.session_state.get("start_term_input", ""),
                                exams=list_exam_credits(st.session_state.profile_code) if st.session_state.profile_code else [])
    st.session_state["gc_used_keys"] = {calgetc_list.course_key(c["code"]) for c in _gc["courses"] if not c.get("areas")}
    with st.expander("Use a different list"):
        st.caption("By default the list comes from ASSIST. For Foothill, its own PDF is the backup. To use a specific "
                   "PDF instead, for example a newer year's, paste its link here.")
        st.text_input("Link to a Cal-GETC course list (PDF)", key="gcl_url_input", on_change=persist_settings,
                      placeholder=calgetc_list.DEFAULT_URL)
        if st.button("Download the list again", key="gcl_refresh"):
            reload_list()
            st.rerun()

    # ---- should the plan include the missing Cal-GETC courses? ----
    st.markdown("**Cal-GETC in your plan**")
    include_gc = st.toggle("Include the missing Cal-GETC courses in my plan", key="gc_include",
                           on_change=persist_settings)
    _slots = missing_slots(_gc["saved"], _gc["courses"])
    if not include_gc:
        st.caption("Off: the plan covers your major's requirements only.")
    else:
        if _gc["dirty"]:
            st.warning("The board has changes you have not saved. The plan only uses saved placements, so click "
                       "“Save my placements” first.")
        if _slots:
            _plan_u, (lo, hi) = unit_plan(SYS)
            counts = {}
            for sl in _slots:
                counts.setdefault(sl["area"], [sl["short"], 0])[1] += 1
            _names = st.session_state.get("placeholder_names") or {}
            _units = st.session_state.get("placeholder_units") or {}
            def _slot_units(area, i):
                return float(_units.get(f"CAL-GETC {area} #{i}") or 0)
            def _units_text(area, n):
                picked = [_slot_units(area, i) for i in range(1, n + 1)]
                return f"{lo}-{hi} each" if not any(picked) else ", ".join(f"{u:g}" if u else f"{lo}-{hi}" for u in picked)
            st.table([{"Cal-GETC area": area, "Still needed": f"{n} {short.lower()} course{'s' if n > 1 else ''}",
                       "Units": _units_text(area, n),
                       **({"Your pick": ", ".join(_names[f"CAL-GETC {area} #{i}"] for i in range(1, n + 1)
                                                  if _names.get(f"CAL-GETC {area} #{i}"))}
                          if any(k.startswith("CAL-GETC") for k in _names) else {})}
                      for area, (short, n) in counts.items()])
            _lo_tot = sum(_slot_units(sl["area"], sl["n"]) or lo for sl in _slots)
            _hi_tot = sum(_slot_units(sl["area"], sl["n"]) or hi for sl in _slots)
            _range = f"{_lo_tot:g}" if _lo_tot == _hi_tot else f"{_lo_tot:g}-{_hi_tot:g}"
            st.caption(T(f"{len(_slots)} course{'s' if len(_slots) > 1 else ''} to add, about {_range} units in all "
                       f"(about {_lo_tot * HOURS_PER_UNIT:g}{'' if _lo_tot == _hi_tot else f'-{_hi_tot * HOURS_PER_UNIT:g}'} hours of work a week "
                       f"in total, {HOURS_PER_UNIT} hours a week for every unit). "
                       "Each goes into your plan as a placeholder, like “1 arts course”. Pick the real course "
                       "from Foothill's list under your plan to use its exact units. Area 5C (the lab) is not "
                       "counted: it is usually part of a 5A or 5B science course."))
        else:
            st.success("Every Cal-GETC area has the usual number of courses placed, so nothing is added to your plan.")
    reqs = list(reqs) + (placeholder_reqs(_slots, st.session_state.get("placeholder_names"),
                                                           st.session_state.get("placeholder_units"), system=SYS) if include_gc else [])  # everything below plans with these

    # Drop codes that are not in this major's list BEFORE the box is drawn. (Passing default= as well as
    # a session value is what made Streamlit print a warning on every run.)
    st.session_state["completed_input"] = [c for c in (st.session_state.completed_input or []) if c in all_codes]
    completed = st.multiselect(T("Completed Foothill courses that match the listed requirements"), all_codes,
        key="completed_input",
        help="Only select exact course codes shown in the ASSIST options. Other-college courses need official equivalency review.")
    s1, s2, s3, s4 = st.columns(4)
    max_units = s1.slider(T("Max units per quarter"), 8, 20, key="max_units_input")
    min_units = s2.slider(T("Min units per quarter (0 = none)"), 0, 20, key="min_units_input", on_change=persist_settings,
                          help=T("Some schools need a minimum load, for example for full-time status or financial aid. "
                               "The last quarter with classes may be lighter."))
    start_term = s3.text_input(T("First quarter of the plan"), key="start_term_input")
    goal_term = s4.text_input("Transfer goal", key="goal_term_input")
    if min_units > max_units:
        st.warning("The minimum is higher than the maximum, so the maximum is used for both.")
        min_units = max_units
    allow_summer = st.checkbox(T("Plan summer quarters too (off = Fall, Winter and Spring only)"),
                               key="allow_summer_input", on_change=persist_settings)
    notes = st.text_area("Anything that affects your schedule (work hours, family, etc.)", key="notes_input")

    if st.button("Build my plan", type="primary"):
        with st.spinner("Building and checking your plan..."):
            try:
                st.session_state.res = build_plan(st.session_state.major_label,
                                                  (st.session_state.get("agreement_info") or {}).get("receiving", {}).get("name") or CAMPUS,
                                                  reqs, completed,
                                                  max_units, start_term, goal_term, notes, min_units,
                                                  allow_summer=allow_summer, term_system=SYS, college=COLLEGE_FULL)
                st.session_state.completed_now = list(completed)
                st.session_state.max_units_now = max_units
                st.session_state.min_units_now = min_units
                st.session_state.allow_summer_now = allow_summer
                st.session_state.goal_now = goal_term
                st.session_state["plan_ap_sig"] = inputs_signature(reqs)
                st.session_state.profile_code = save_profile(st.session_state.profile_code,
                    major_key=st.session_state.major_key, major_label=st.session_state.major_label,
                    completed=list(completed), max_units=max_units, start_term=start_term,
                    goal_term=goal_term, notes=notes, plan_res=st.session_state.res)
                persist_settings()
                st.success(f"Plan saved. Profile code: {st.session_state.profile_code}")
            except Exception as e:
                st.error(f"Could not build the plan: {str(e)[:200]}. Please try again.")

    if st.button("Save profile without rebuilding"):
        had_profile = bool(st.session_state.profile_code)
        st.session_state.profile_code = save_profile(st.session_state.profile_code,
            major_key=st.session_state.major_key, major_label=st.session_state.major_label,
            completed=list(completed), max_units=max_units, start_term=start_term,
            goal_term=goal_term, notes=notes, plan_res=st.session_state.res)
        persist_settings()
        if had_profile:
            st.success(f"Profile saved: {st.session_state.profile_code}")
        else:
            st.session_state["profile_notice"] = (
                f"A new profile was created. Keep this code to come back later: `{st.session_state.profile_code}`")
            st.rerun()


# ---------- 4. Plan ----------
if st.session_state.res:
    st.header("4. Your plan")
    _sig = st.session_state.get("plan_ap_sig")
    if _sig is not None and _sig != inputs_signature(reqs or []):
        st.info("Your AP credit or Cal-GETC choices changed after this plan was built. "
                "Click “Build my plan” again to use them.")
    _recs = list_courses(st.session_state.profile_code) if st.session_state.profile_code else []
    _earned, _in_prog = units_so_far(_recs)
    if not _recs:   # no transcript saved: estimate from the courses ticked as completed
        _earned = units_for_codes(reqs or [], st.session_state.completed_now or [])
    _past = sum(c["units"] for q in st.session_state.res["plan"].get("past_quarters", [])
                for c in q["courses"] if c.get("result") == "passed")
    _note = ("Total by end of quarter = " + f"{_earned:g} units already earned"
             + (f" + {_in_prog:g} in progress" if _in_prog else "")
             + (f" + {_past:g} from finished quarters of this plan" if _past else "")
             + " + the planned quarters. Only UC-transferable units count toward transfer, so confirm the "
               "total with a counselor." if (_earned or _in_prog or _past) else
             "No earned units are saved yet (upload a transcript to include them), so the totals count only these quarters.")
    _note = T(_note)
    # which of these units are confirmed UC-transferable (courses in ASSIST's agreement for this major)
    _plan_now = st.session_state.res["plan"]
    if _recs:
        _taken = [(c["code"], c["units"]) for c in placeable(_recs)["courses"]]
    else:
        _taken = [(code, units_for_codes(reqs or [], [code])) for code in (st.session_state.completed_now or [])]
    _taken += [(c["code"], c["units"]) for q in _plan_now.get("past_quarters", []) for c in q["courses"] if c.get("result") == "passed"]
    _planned = [(c["code"], c["units"]) for q in _plan_now["quarters"] for c in q["courses"]]
    _tn = transferable.transfer_note(_taken, _planned, reqs or [], system=SYS, receiving=RECEIVING_KIND)
    if _tn:
        _note = _note + "\n\n" + _tn
    render_plan(st.session_state.res, reqs, set(st.session_state.completed_now or []),
                st.session_state.max_units_now or 16, st.session_state.get("min_units_now") or 0,
                _earned + _in_prog + _past, _note)
    render_options(st.session_state.res, reqs)

    # ---------- 5. Adjust ----------
    st.header("5. Life happens: adjust your plan")
    plan_now = st.session_state.res["plan"]
    terms = [q["term"] for q in plan_now["quarters"]]
    options = ["None yet"] + terms
    if st.session_state.pop("reset_through", False) or st.session_state.get("through_term") not in options:
        st.session_state["through_term"] = "None yet"
    through = st.selectbox(T("Which quarters are already over?"), options, key="through_term",
                           help=T("Everything up to and including this quarter is finished."))
    past_terms = terms[:terms.index(through) + 1] if through != "None yet" else []
    past_codes = [c["code"] for q in plan_now["quarters"] if q["term"] in past_terms
                  for c in q["courses"]]
    done_now = st.session_state.completed_now or []

    # No default answer: the student must say how each finished course went.
    results = {}
    if past_codes:
        st.markdown("**How did each finished course go?**")
        titles = {c["code"]: (c.get("title") or c["code"]) for q in plan_now["quarters"] for c in q["courses"]}
        for code in past_codes:
            results[code] = st.radio(titles.get(code, code), ["Passed", "Failed or dropped"], index=None,
                                     horizontal=True, key=f"result-{through}-{code}")
    if st.session_state.pop("reset_change", False):
        st.session_state["change_note"] = ""   # the note was used by the last adjustment, so start clean
    change = st.text_input("What changed?", key="change_note",
                           placeholder=T("I failed MATH 1A and can only take 12 units next quarter"))

    known = {c["code"] for r in reqs for g in r["options"] for c in g}
    planned = {c["code"]: q["term"] for q in plan_now["quarters"] for c in q["courses"]}

    # A course finished in an earlier quarter can only change if the note says so. This question appears
    # only then, with the answer already filled in.
    earlier = sorted(set(done_now) - set(past_codes))
    earlier_failed = []
    for c in conflicting_codes(change, earlier, set(), known):
        if st.checkbox(f"Yes, {c} (finished earlier) was failed or dropped", value=True, key=f"earlier-fail-{c}"):
            earlier_failed.append(c)

    failed = {c for c, v in results.items() if v == "Failed or dropped"} | set(earlier_failed)
    unanswered = [c for c in past_codes if results[c] is None]
    passed_past = [c for c in past_codes if results[c] == "Passed"]

    sl1, sl2 = st.columns(2)
    new_max = sl1.slider(T("Max units per quarter now"), 8, 20, st.session_state.max_units_now or 16)
    new_min = sl2.slider(T("Min units per quarter now (0 = none)"), 0, 20, int(st.session_state.get("min_units_now") or 0))
    if new_min > new_max:
        st.warning("The minimum is higher than the maximum, so the maximum is used for both.")
        new_min = new_max

    # Guard: the note must not contradict the answers above, or the plan itself.
    conflicts = conflicting_codes(change, sorted(set(past_codes) | set(done_now) | set(planned)),
                                  failed, known)
    not_over = [c for c in conflicts if c in planned and planned[c] not in past_terms]
    marked_passed = [c for c in conflicts if c not in not_over]
    confirmed = True
    if not_over:
        first = min((planned[c] for c in not_over), key=terms.index)
        st.warning(f"Your note says you failed or dropped {', '.join(not_over)}, but {first} "
                   "is not marked as over, so the plan would not count it as failed.")
        st.button(f"Mark {first} as over", on_click=set_through, args=(first,))
    if marked_passed:
        st.warning("Your note mentions failing or dropping " + ", ".join(marked_passed) +
                   ", but it is not marked as failed. Fix it above, or confirm below.")
    if conflicts:
        confirmed = st.checkbox("My note is about something else. Nothing needs to change above.",
                                key=f"confirm-{through}-{change}")
    if unanswered:
        st.info("Choose Passed or Failed for: " + ", ".join(unanswered))

    new_completed = sorted((set(done_now) | set(passed_past)) - failed)
    if past_codes or failed:
        st.caption(f"The plan will treat these as **completed**: {', '.join(new_completed) or 'none'}. "
                   f"As **failed, to retake**: {', '.join(sorted(failed)) or 'none'}.")

    # Guard 2: the note says something already happened, but no quarter is marked as over.
    # (skipped when the note names a scheduled course: the "Mark ... as over" warning above covers it)
    needs_quarter = (bool(change.strip()) and not past_terms and not not_over and not earlier_failed
                     and implies_finished(change))   # a confirmed earlier failure already explains the note
    nothing_finished = True
    if needs_quarter:
        hints = scheduled_terms_mentioned(change, plan_now["quarters"], known)
        msg = T("Your note says something already happened (like failing or finishing a course), "
                "but no quarter is marked as over.")
        if hints:
            msg += " " + "; ".join(f"{c} is scheduled in {t}" for c, t in hints.items()) + \
                   T(". Pick that quarter in the first box so it gets recorded.")
        st.warning(msg)
        nothing_finished = st.checkbox("Nothing is finished yet. My note is about the future.",
                                       key=f"future-{change}")

    blocked = bool(unanswered) or not confirmed or not nothing_finished
    if st.button("Adjust my plan", disabled=blocked) and (change.strip() or past_terms or failed):
        only_passed = (bool(past_terms) and not failed and not change.strip()
                       and new_max == (st.session_state.max_units_now or 16)
                       and new_min == int(st.session_state.get("min_units_now") or 0))
        apply_plan_change(plan_now, reqs, new_completed, change, new_max, new_min,
                          past_terms=past_terms, failed=failed, only_passed=only_passed)