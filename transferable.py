"""Which units are CONFIRMED UC-transferable.

ASSIST's agreement for a major lists the community college courses that UC accepts for that major, so a course in it
is UC-transferable. Other courses (electives, many general education courses) usually transfer too, but nothing in the
agreement says so, and UC puts limits on some of them. Those are counted as "not confirmed", never as "not transferable".
The UC transferable course list (the UC TCA on assist.org) is the place to check them.

UC needs at least 60 semester units of transferable credit to admit a transfer student. Foothill and UCSD use quarters,
so that is 90 quarter units. (Source: Cal-GETC Standards v1.3, section 8.2 mentions the 60 transferable semester units
required for transfer admission.) AP and IB credit is not counted here: its units depend on UC's own policy.
"""
from calgetc_list import course_key

UC_MIN_QUARTER_UNITS = 90.0
UC_MIN_SEMESTER_UNITS = 60.0


def _label(receiving):
    return {"UC": "UC", "CSU": "CSU"}.get(receiving or "", "")


def confirmed_keys(reqs):
    """Course keys of every community college course in the agreement's options (placeholders are not courses yet)."""
    keys = set()
    for r in reqs or []:
        if r.get("placeholder"):
            continue
        for group in r.get("options", []):
            for c in group:
                keys.add(course_key(c["code"]))
    return keys


def split_units(items, keys):
    """items: (code, units) pairs. Returns (confirmed units, not-confirmed units)."""
    conf = other = 0.0
    for code, units in items:
        u = float(units or 0)
        if course_key(code) in keys:
            conf += u
        else:
            other += u
    return conf, other


def transfer_note(taken, planned, reqs, system="quarter", receiving="UC"):
    """A sentence for under the plan totals.

    taken:   (code, units) for courses finished, in progress, or passed in finished quarters of the plan.
    planned: (code, units) for courses still planned. Placeholders are passed with their placeholder code, which is
             never in the agreement, so they count as not confirmed.
    system:  "quarter" or "semester", the units the college uses. receiving: "UC", "CSU" or anything else. The
             60-semester-unit minimum is stated for UC only, because that is the source this was checked against."""
    keys = confirmed_keys(reqs)
    c1, o1 = split_units(taken, keys)
    c2, o2 = split_units(planned, keys)
    conf, other = c1 + c2, o1 + o2
    total = conf + other
    if not total:
        return ""
    who = _label(receiving)
    tag = f"{who}-transferable" if who else "transferable"
    text = (f"By the end of this plan, {conf:g} of your {total:g} units are confirmed {tag}, because those courses "
            f"are in ASSIST's agreement for this major. The other {other:g} are not confirmed (most college courses "
            f"transfer, but some have limits, so check them on the {who + ' ' if who else ''}transferable course list). AP and IB units are not "
            "counted.")
    if who != "UC":
        return text + " Check the transfer unit minimum for this campus."
    need = UC_MIN_SEMESTER_UNITS if system == "semester" else UC_MIN_QUARTER_UNITS
    other_sys = ("90 quarter units" if system == "semester" else "60 semester units")
    text += f" UC needs at least {need:g} {system} units ({other_sys}) of transferable credit."
    if conf >= need:
        text += " The confirmed units alone are enough."
    elif total >= need:
        text += f" The confirmed units are {need - conf:g} short, so check that the rest transfer."
    else:
        text += f" You would be {need - total:g} units short even if everything transfers."
    return text
