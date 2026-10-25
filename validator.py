"""Checks a plan with plain Python, no Claude. Claude is good at ordering and
explaining, but exact rules (like "MATH 1C AND MATH 1D are both required")
should be verified by code."""
from collections import defaultdict


def _fmt_options(r):
    return " OR ".join(" + ".join(c["code"] for c in g) for g in r["options"])


def check_plan(plan, reqs, completed, max_units, min_units=0):
    """Return a list of problems found in the plan (empty list = plan passes)."""
    problems = []
    completed = set(completed)
    allowed = {c["code"] for r in reqs for g in r["options"] for c in g}
    scheduled = []

    for q in plan.get("quarters", []):
        total = 0
        for c in q.get("courses", []):
            code = c["code"]
            scheduled.append(code)
            total += c.get("units", 0)
            if code not in allowed:
                problems.append(f"{code} in {q['term']} is not a course listed in any requirement option.")
            if code in completed:
                problems.append(f"{code} in {q['term']} is already completed.")
        if total > max_units:
            problems.append(f"{q['term']} has {total} units, over the limit of {max_units}.")
        if abs(total - q.get("total_units", total)) > 0.01:
            problems.append(f"{q['term']} says total_units={q.get('total_units')} but the courses add up to {total}.")

    if min_units:  # every quarter with classes, except the last one, must reach the minimum
        with_classes = [q for q in plan.get("quarters", []) if q.get("courses")]
        for q in with_classes[:-1]:
            total = sum(c.get("units", 0) for c in q["courses"])
            if total < min_units:
                problems.append(f"{q['term']} has {total:g} units, under the minimum of {min_units} per quarter.")

    for code in sorted({c for c in scheduled if scheduled.count(c) > 1}):
        problems.append(f"{code} is scheduled more than once.")

    have = completed | set(scheduled)
    sections = defaultdict(list)
    for r in reqs:
        sections[r["section_id"]].append(r)

    for items in sections.values():
        doable = [r for r in items if r["status"] == "articulated"]
        if not doable:
            continue
        pick = items[0]["pick_n_of_section"]
        need = min(pick, len(doable)) if pick else len(doable)
        met = [r for r in doable
               if r.get("ap_met") or any(all(c["code"] in have for c in g) for g in r["options"])]
        if len(met) < need:
            unmet = [r for r in doable if r not in met]
            detail = "; ".join(f"{r['course']} needs {_fmt_options(r)}" for r in unmet)
            problems.append(
                f"Section needs {need} requirement(s) met but the plan covers {len(met)}. "
                f"Courses inside one option are ALL required. Not covered: {detail}"
            )
    return problems