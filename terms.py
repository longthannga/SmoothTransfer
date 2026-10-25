"""Quarter calendar helpers. Foothill terms in calendar order: Winter, Spring, Summer, Fall.

A term is written like "Winter 2027". Anything else (for example "Quarter 2") is simply not checked,
so nothing here ever raises on odd text.
"""
import re

SEASONS = ["Winter", "Spring", "Summer", "Fall"]
_TERM = re.compile(r"^\s*(winter|spring|summer|fall)\s+(\d{4})\s*$", re.I)


def parse_term(text):
    """(season, year) for 'Winter 2027', or None if the text is not a term."""
    m = _TERM.match(str(text or ""))
    return (m.group(1).capitalize(), int(m.group(2))) if m else None


def term_index(season, year):
    """A number that grows by 1 each quarter, so terms can be compared and counted."""
    return year * 4 + SEASONS.index(season)


def term_label(index):
    return f"{SEASONS[index % 4]} {index // 4}"


def last_term_allowed(goal_term, allow_summer):
    """The last quarter the student can take classes in, which is the quarter before the transfer goal.
    Summer quarters are skipped when they are not allowed. None if the goal is not a term."""
    goal = parse_term(goal_term)
    if not goal:
        return None
    idx = term_index(*goal) - 1
    while not allow_summer and SEASONS[idx % 4] == "Summer":
        idx -= 1
    return idx


def last_term_label(goal_term, allow_summer):
    idx = last_term_allowed(goal_term, allow_summer)
    return term_label(idx) if idx is not None else None


def shift_year(term, years=1):
    """The same season, some years later: 'Fall 2028' -> 'Fall 2029'. None if it is not a term."""
    t = parse_term(term)
    return f"{t[0]} {t[1] + years}" if t else None


def term_problems(plan_terms, goal_term, allow_summer):
    """Plain-English problems with the terms a plan uses. Terms that are not written like
    'Winter 2027' are skipped."""
    problems = []
    last = last_term_allowed(goal_term, allow_summer)
    for t in plan_terms:
        p = parse_term(t)
        if not p:
            continue
        if p[0] == "Summer" and not allow_summer:
            problems.append(f"{t} is a summer quarter, but summer quarters are off. Use Fall, Winter and Spring only.")
        elif last is not None and term_index(*p) > last:
            problems.append(f"{t} is after {term_label(last)}, the last quarter allowed before the transfer goal "
                            f"({goal_term.strip()}). Finish every course by {term_label(last)}.")
    return problems
