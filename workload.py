"""The study-time rule used everywhere: about 4 hours a week for each unit (class time plus
homework and study). Change HOURS_PER_UNIT here and the plan, the board and Claude all follow."""

HOURS_PER_UNIT = 4


def quarter_hours(courses):
    """Weekly hours for a list of planned courses.

    weekly_hours      uses each course's planned units.
    weekly_hours_max  uses units_max when a course is a Cal-GETC placeholder (the real course may be
                      up to 6 units), so it is never lower than weekly_hours."""
    units = sum(float(c.get("units") or 0) for c in courses)
    high = sum(float(c.get("units_max") or c.get("units") or 0) for c in courses)
    return {"weekly_hours": round(units * HOURS_PER_UNIT, 1),
            "weekly_hours_max": round(high * HOURS_PER_UNIT, 1),
            "total_units_max": high}


def units_for_codes(reqs, codes):
    """Units of the given Foothill course codes, taken from the ASSIST options (each code counted once).
    Used as a fallback when no transcript is saved."""
    by = {}
    for r in reqs:
        for g in r["options"]:
            for c in g:
                by.setdefault(c["code"], float(c.get("units") or 0))
    return sum(by.get(c, 0.0) for c in set(codes))


def quarter_totals(quarters, units_before=0.0, min_units=0):
    """Rows for the totals table under the plan board: units this quarter, the running total by the end
    of the quarter (starting from units_before), weekly hours, and how the quarter compares with the minimum.
    The last quarter that has classes may be lighter than the minimum (it is flagged but allowed)."""
    rows, running = [], float(units_before)
    with_classes = [q for q in quarters if q.get("courses")]
    last = with_classes[-1] if with_classes else None
    for q in quarters:
        units = sum(float(c.get("units") or 0) for c in q.get("courses", []))
        running += units
        if not q.get("courses"):
            status = "no classes"
        elif not min_units:
            status = ""
        elif units >= min_units:
            status = "meets the minimum"
        else:
            status = "below the minimum (last quarter)" if q is last else "below the minimum"
        rows.append({"term": q["term"], "units": units, "total_by_end": running,
                     "weekly_hours": round(units * HOURS_PER_UNIT, 1), "status": status})
    return rows