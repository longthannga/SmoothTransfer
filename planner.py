"""The Claude part: orders a fixed list of needed courses into quarters.

Python decides WHICH courses are needed (exact facts, no guessing).
Claude decides WHEN to take them, checks workload, and explains why.
If the checker finds a problem, Claude gets one chance to repair the plan.
"""
import json
import os
from collections import defaultdict

import anthropic
from dotenv import load_dotenv

from requirements_parser import remaining
from validator import check_plan
from workload import HOURS_PER_UNIT, quarter_hours
from terms import last_term_label, term_problems

load_dotenv()
client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
MODEL = "claude-haiku-5-5"

SYSTEM = """You are a transfer-planning assistant for California community college students.
You receive JSON with: the target UC campus and major, a FIXED list "courses_to_schedule",
the courses already completed, a short list of requirements that cannot be done at the community college,
and the student's limits.

Rules:
- Schedule EVERY course in "courses_to_schedule", each exactly once. Do not add, swap, or
  drop courses. The choice of courses has already been made.
- The community college is named in "target". Plan by its term system, given as "term_system".
- Prerequisite chains are not in the data. If you order courses using common sequences
  (for example MATH 1A before 1B), mark prereq_assumed true so the student verifies it.
- Respect max_units per quarter. Mark each quarter light, moderate, or heavy and say why
  (for example several lab or heavy-math courses in one quarter).
- Take the student's notes seriously (work hours, family). Heavy work hours mean lighter quarters.
- Use plain, simple language. Keep each "why" under 12 words.
- Requirements in "cannot_do_at_foothill_or_unknown" cannot be done at the community college. Do not schedule them.
  Return empty lists for "cannot_do_at_foothill" and "needs_check": the program fills those in.
- "covered_by_ap" lists requirements the student already meets with AP exam credit. Never schedule
  courses for them. If it is not empty, say so briefly in the summary.
- Time rule: plan about 4 hours of work each week for every unit (class time plus homework and study).
  "hours_per_unit_per_week" in the input is that number. A 5-unit course is about 20 hours a week and a
  12-unit quarter is about 48 hours a week. In each quarter's "workload_note", give the weekly hours
  (units times the rule) and add any work or other hours the student mentions in their notes. A
  full-time job is about 40 hours a week, so say plainly when school plus work looks like too much,
  and suggest what to move. Use whole numbers and plain words.
- "term_system" is "quarter" or "semester". Where these rules say quarter, use the term system: a semester college
  has Fall and Spring semesters (Summer is optional, and Winter is not a term). All units are in that system's units.
- Name every term with its season and year, for example "Winter 2027" (or "Fall 2027" for a semester).
- Plan only in terms up to "last_term_allowed" (the last quarter before the transfer goal). Do not use a
  Summer quarter unless "allow_summer_quarters" is true.
- If "student_work_hours_per_week" is given, use it instead of any work hours in the student's notes.
- Do not recommend one fix over the others. If the load is too heavy or the transfer goal is at risk, say so
  plainly and say that the app lists the student's options under the plan (work fewer hours, add summer
  quarters, or move the transfer a year later). Never call one of them the best.
- If "min_units_per_quarter" is above 0, give every quarter that has classes at least that many units. The
  last quarter with classes may be lighter if that is all that is left. Cal-GETC placeholders are flexible,
  so use them to fill a quarter. If a quarter cannot reach the minimum, say so in its "workload_note".
- A course with "placeholder": true is a Cal-GETC general education slot that the student has not
  picked a course for yet. Schedule it like any other course, using the "units" given (the real
  course will be 4 to 6 units). In its "why", say what kind of course it is. Do not name a specific course.
- Never leave out spaces between words.

Return ONLY valid JSON with this shape, no other text:
{"summary": str,
 "quarters": [{"term": str, "courses": [{"code": str, "satisfies": str, "units": number,
    "why": str, "prereq_assumed": bool}], "total_units": number,
    "workload": "light"|"moderate"|"heavy", "workload_note": str}],
 "cannot_do_at_foothill": [{"course": str, "note": str}],
 "needs_check": [{"course": str, "note": str}],
 "warnings": [str]}"""

ADJUST_SYSTEM = SYSTEM + """

You are now ADJUSTING an existing plan because the student's situation changed.
- Keep quarters the change does not affect exactly as they are.
- "quarters_already_passed" are over and are NOT in current_plan. Never schedule anything in them.
- Only the quarters in "quarters_already_passed" are over. Never assume another quarter has passed,
  even if student_change mentions one. If it conflicts with the lists, say so in "warnings" and do not
  move courses because of it.
- "failed_courses" were failed or dropped and are back in courses_to_schedule. Schedule them again.
- Return ONLY the quarters that have not passed yet, starting with the next one.
- NEVER decide on your own that a quarter is over. Only "quarters_already_passed" are over.
  Keep every quarter in current_plan, even if it ends up empty. If the student's note says
  something happened in a quarter that is not in "quarters_already_passed", keep that quarter
  and add a warning asking them to mark it as over.
- "courses_to_schedule" is the full list still needed now. Make sure each one appears once.
- Add one more key to the JSON: "what_changed": [str], short plain-language sentences
  saying what you moved, added, or removed and why. Also include a backup path in
  "warnings" if the change puts the transfer goal at risk."""


# ---------- Python decides the courses ----------

def _met(r, have):
    # ap_met: the student's AP score exempts this requirement (see ap.py)
    return bool(r.get("ap_met")) or any(all(c["code"] in have for c in g) for g in r["options"])


def _best_option(r, have):
    """The option that needs the fewest courses we don't already have."""
    return min(r["options"], key=lambda g: sum(c["code"] not in have for c in g))


def needed_courses(reqs, completed):
    """Unique Foothill courses still needed. One option per requirement, and
    'pick N of section' rules are respected (same logic as validator.py)."""
    done = set(completed)
    need = {}  # code -> {"code", "units", "satisfies": [...]}

    sections = defaultdict(list)
    for r in reqs:
        sections[r["section_id"]].append(r)

    for items in sections.values():
        doable = [r for r in items if r["status"] == "articulated" and r["options"]]
        if not doable:
            continue
        pick = items[0]["pick_n_of_section"]
        required = min(pick, len(doable)) if pick else len(doable)
        left = required - sum(_met(r, done) for r in doable)
        candidates = [r for r in doable if not _met(r, done)]

        while left > 0 and candidates:
            have_now = done | set(need)
            # cheapest first, so shared courses (BIOL 1A+1B+1C) get reused
            candidates.sort(key=lambda r: sum(c["code"] not in have_now
                                              for c in _best_option(r, have_now)))
            r = candidates.pop(0)
            for c in _best_option(r, have_now):
                if c["code"] in done:
                    continue
                entry = need.setdefault(c["code"], {"code": c["code"], "units": c["units"],
                                                    "satisfies": []})
                if r.get("placeholder"):  # a Cal-GETC slot: the student has not picked the course yet
                    entry.update({"placeholder": True, "title": r["title"],
                                  "units_range": _units_label(r["units_min"], r["units_max"])})
                if r["course"] not in entry["satisfies"]:
                    entry["satisfies"].append(r["course"])
            left -= 1

    return list(need.values())


def not_schedulable(reqs):
    """Requirements the student needs but cannot do at Foothill (ASSIST: "No Course Articulated", or
    "must be taken after transfer"). A course in a "pick N" section is only an optional alternative
    when enough other courses in that section can be done at Foothill, so it is left out."""
    by_section = defaultdict(list)
    for r in reqs:
        by_section[r["section_id"]].append(r)
    seen, out = set(), []
    for r in reqs:
        if r["status"] == "articulated" or r["course"] in seen or r.get("ap_met"):
            continue
        items = by_section[r["section_id"]]
        pick = items[0]["pick_n_of_section"]
        if pick and sum(x["status"] == "articulated" for x in items) >= pick:
            continue    # the section can be finished without this course
        seen.add(r["course"])
        out.append({"course": r["course"], "status": r["status"], "note": r["note"] or ""})
    return out


# ---------- Claude call ----------

def _call(system, messages):
    with client.messages.stream(model=MODEL, max_tokens=16000,
                                system=system, messages=messages) as s:
        msg = s.get_final_message()
    if os.getenv("SMOOTH_DEBUG"):   # off by default; set SMOOTH_DEBUG=1 to watch the token counts
        print("usage:", msg.usage)
    text = "".join(b.text for b in msg.content if b.type == "text")
    if msg.stop_reason == "max_tokens":
        raise ValueError("Claude's answer was cut off. Send fewer courses or raise max_tokens.")
    if not text:
        raise ValueError(f"No text in the response (stop_reason={msg.stop_reason})")
    return text


def _extract_json(text):
    """Return the first JSON object in the reply and ignore anything after it
    (code fences, a closing note, etc.)."""
    start = text.find("{")
    if start == -1:
        raise ValueError("Claude's answer had no JSON in it.")
    try:
        obj, end = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError:
        print("BAD JSON FROM CLAUDE:\n", text)  # so you can see what it sent
        raise
    if text[end:].strip().strip("`").strip():
        print("(ignored text after the JSON:", text[end:][:200].strip(), ")")
    return obj


def _all_checks(plan, reqs, completed, max_units, must_schedule, required_terms=(), forbidden_terms=(), min_units=0,
                goal_term="", allow_summer=False, term_system="quarter"):
    problems = check_plan(plan, reqs, completed, max_units, min_units)
    terms = [q["term"] for q in plan.get("quarters", [])]
    problems += term_problems(terms, goal_term, allow_summer, term_system)
    for t in required_terms:
        if t not in terms:
            problems.append(f"{t} was removed, but the student did not say it is over. "
                            "Keep it in the plan (use an empty quarter if nothing is scheduled).")
    for t in forbidden_terms:
        if t in terms:
            problems.append(f"{t} is already over. Do not include it.")
    scheduled = {c["code"] for q in plan.get("quarters", []) for c in q.get("courses", [])}
    missing = sorted(must_schedule - scheduled)
    if missing:
        problems.append("These required courses are missing from the plan: " + ", ".join(missing))
    return problems


def _generate_checked(system, payload, reqs, completed, max_units, max_repairs=1,
                      required_terms=(), forbidden_terms=(), min_units=0, goal_term="", allow_summer=False,
                       term_system="quarter"):
    """Ask Claude for a plan, check it with code, and send problems back for repair."""
    must_schedule = {c["code"] for c in payload["courses_to_schedule"]}
    messages = [{"role": "user", "content": json.dumps(payload)}]
    text = _call(system, messages)
    plan = _extract_json(text)
    found = _all_checks(plan, reqs, completed, max_units, must_schedule,
                        required_terms, forbidden_terms, min_units, goal_term, allow_summer, term_system)
    first_found = list(found)

    for _ in range(max_repairs):
        if not found:
            break
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "A checker found problems with that plan. Fix them and "
                                        "return the full corrected JSON only:\n- " + "\n- ".join(found)},
        ]
        text = _call(system, messages)
        plan = _extract_json(text)
        found = _all_checks(plan, reqs, completed, max_units, must_schedule,
                        required_terms, forbidden_terms, min_units, goal_term, allow_summer, term_system)

    return {"plan": plan, "issues_caught": first_found, "issues_remaining": found}


def _cannot_note(item):
    if item["status"] == "after_transfer":
        return "ASSIST says this course must be taken at the university after transfer."
    return ("ASSIST: No Course Articulated. Foothill has no approved course for it, so plan to take it "
            "at the university after transfer.")


def _units_label(lo, hi):
    """'4-6' for a range, '4.5' when the student picked a real course with exact units."""
    return f"{lo:g}" if lo == hi else f"{lo:g}-{hi:g}"


def finish_plan(plan, reqs):
    """Python's touch-ups after Claude answers: Cal-GETC placeholder courses get their readable title
    and 4-6 unit range, and every quarter gets its weekly hours (units times the study-time rule)."""
    ph = {}
    for r in reqs:
        if r.get("placeholder"):
            for g in r["options"]:
                for c in g:
                    ph[c["code"]] = r
    for q in plan.get("quarters", []):
        for c in q.get("courses", []):
            r = ph.get(c.get("code"))
            if r:
                c.update({"placeholder": True, "title": r["title"], "units_min": r["units_min"], "units": r["units"],
                          "area_id": r["area_id"], "generic_title": r.get("generic_title", r["title"]),
                          "units_max": r["units_max"], "units_label": _units_label(r["units_min"], r["units_max"]),
                          "satisfies": f"Cal-GETC {r['area_id']}: {r['short']}"})
        q.update(quarter_hours(q.get("courses", [])))
    # These two lists come from the ASSIST data, not from Claude.
    plan["cannot_do_at_foothill"] = [{"course": i["course"], "note": _cannot_note(i)} for i in not_schedulable(reqs)
                                     if i["status"] in ("none", "after_transfer")]
    plan["needs_check"] = [{"course": i["course"], "note": "ASSIST has no information on this course. Ask a counselor."}
                           for i in not_schedulable(reqs) if i["status"] not in ("none", "after_transfer")]
    return plan


def covered_by_ap(reqs):
    """Requirements met by AP credit, for Claude to mention. Python already left them out."""
    seen, out = set(), []
    for r in reqs:
        if r.get("ap_met") and r["course"] not in seen:
            seen.add(r["course"])
            out.append({"requirement": r["course"], "by": r["ap_by"]})
    return out


def build_plan(major_label, campus, reqs, completed, max_units, start_term, goal_term, notes="", min_units=0,
               allow_summer=False, work_hours=None, term_system="quarter", college=None):
    completed = set(completed)
    payload = {
        "target": {"campus": campus, "major": major_label, **({"community_college": college} if college else {})},
        "start_term": start_term,
        "transfer_goal": goal_term,
        "max_units_per_quarter": max_units,
        "min_units_per_quarter": min_units,
        "allow_summer_quarters": allow_summer,
        "term_system": term_system,
        "last_term_allowed": last_term_label(goal_term, allow_summer, term_system),
        **({"student_work_hours_per_week": work_hours} if work_hours is not None else {}),
        "hours_per_unit_per_week": HOURS_PER_UNIT,
        "student_notes": notes,
        "completed_courses": sorted(completed),
        "courses_to_schedule": needed_courses(reqs, completed),
        "covered_by_ap": covered_by_ap(reqs),
        "cannot_do_at_foothill_or_unknown": not_schedulable(reqs),
    }
    res = _generate_checked(SYSTEM, payload, reqs, completed, max_units, min_units=min_units,
                            goal_term=goal_term, allow_summer=allow_summer, term_system=term_system)
    res["plan"] = finish_plan(res["plan"], reqs)
    return res


def adjust_plan(old_plan, reqs, completed, change, max_units, goal_term, past_terms=(), failed=(), min_units=0,
                allow_summer=False, work_hours=None, term_system="quarter"):
    """`completed` must already reflect the change (passed courses in, failed courses out).
    `past_terms` are quarters that are over. They are frozen into plan["past_quarters"]."""
    completed, failed, past_terms = set(completed), set(failed), list(past_terms)
    frozen, future = [], []
    for q in old_plan["quarters"]:
        if q["term"] in past_terms:
            frozen.append({**q, "courses": [
                {**c, "result": "failed" if c["code"] in failed else "passed"}
                for c in q["courses"]]})
        else:
            future.append(q)
    earlier = old_plan.get("past_quarters", [])
    current = {k: v for k, v in old_plan.items() if k != "past_quarters"}
    current["quarters"] = future

    payload = {
        "current_plan": current,
        "student_change": change,
        "quarters_already_passed": [q["term"] for q in earlier] + past_terms,
        "failed_courses": sorted(failed),
        "transfer_goal": goal_term,
        "max_units_per_quarter": max_units,
        "min_units_per_quarter": min_units,
        "allow_summer_quarters": allow_summer,
        "term_system": term_system,
        "last_term_allowed": last_term_label(goal_term, allow_summer, term_system),
        **({"student_work_hours_per_week": work_hours} if work_hours is not None else {}),
        "hours_per_unit_per_week": HOURS_PER_UNIT,
        "completed_courses": sorted(completed),
        "courses_to_schedule": needed_courses(reqs, completed),
        "covered_by_ap": covered_by_ap(reqs),
        "cannot_do_at_foothill_or_unknown": not_schedulable(reqs),
    }
    res = _generate_checked(ADJUST_SYSTEM, payload, reqs, completed, max_units,
                            required_terms=[q["term"] for q in future],
                            forbidden_terms=payload["quarters_already_passed"], min_units=min_units,
                            goal_term=goal_term, allow_summer=allow_summer, term_system=term_system)
    res["plan"]["past_quarters"] = earlier + frozen
    res["plan"] = finish_plan(res["plan"], reqs)
    return res


# ---------- No Claude needed: everything in the finished quarters went well ----------

def mark_quarters_passed(old_plan, reqs, completed, past_terms, max_units, min_units=0):
    """The student finished some quarters and passed every course, with no other
    change. The rest of the plan stays exactly as it is, so no Claude call is needed."""
    past_terms = list(past_terms)
    frozen, future = [], []
    for q in old_plan["quarters"]:
        if q["term"] in past_terms:
            frozen.append({**q, "courses": [{**c, "result": "passed"} for c in q["courses"]]})
        else:
            future.append(q)
    earlier = old_plan.get("past_quarters", [])
    left_units = sum(q["total_units"] for q in future)
    plan = {**old_plan, "quarters": future, "past_quarters": earlier + frozen}
    plan["summary"] = (f"{len(future)} quarter(s) left, with {left_units:g} units still to take. "
                       f"Finished: {', '.join(q['term'] for q in plan['past_quarters'])}.")
    plan["what_changed"] = [", ".join(past_terms) + " marked as finished. Everything passed, "
                            "so the rest of your plan stays the same."]
    problems = check_plan({"quarters": future}, reqs, completed, max_units, min_units)
    finish_plan(plan, reqs)
    return {"plan": plan, "issues_caught": [], "issues_remaining": problems}