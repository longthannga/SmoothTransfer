"""Term calendar helpers. Terms in calendar order: Winter, Spring, Summer, Fall.

A quarter college has all four. A semester college has Spring, Summer and Fall: Winter is only a short intersession there,
so it is not a term a plan can use. `system` is "quarter" (the default) or "semester".

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


def _usable(idx, allow_summer, system):
    """Whether a plan may use this term: not a summer unless allowed, and not a winter intersession at a semester college."""
    season = SEASONS[idx % 4]
    return not ((season == "Summer" and not allow_summer) or (season == "Winter" and system == "semester"))


def last_term_allowed(goal_term, allow_summer, system="quarter"):
    """The last term the student can take classes in, which is the term before the transfer goal.
    Summer terms are skipped when they are not allowed, and so is winter at a semester college.
    None if the goal is not a term."""
    goal = parse_term(goal_term)
    if not goal:
        return None
    idx = term_index(*goal) - 1
    while not _usable(idx, allow_summer, system):
        idx -= 1
    return idx


def last_term_label(goal_term, allow_summer, system="quarter"):
    idx = last_term_allowed(goal_term, allow_summer, system)
    return term_label(idx) if idx is not None else None


def shift_year(term, years=1):
    """The same season, some years later: 'Fall 2028' -> 'Fall 2029'. None if it is not a term."""
    t = parse_term(term)
    return f"{t[0]} {t[1] + years}" if t else None


def term_problems(plan_terms, goal_term, allow_summer, system="quarter"):
    """Plain-English problems with the terms a plan uses. Terms that are not written like
    'Winter 2027' are skipped."""
    problems = []
    last = last_term_allowed(goal_term, allow_summer, system)
    word = "semester" if system == "semester" else "quarter"
    for t in plan_terms:
        p = parse_term(t)
        if not p:
            continue
        if p[0] == "Winter" and system == "semester":
            problems.append(f"{t} is a winter intersession, not a semester. Use Fall and Spring semesters only.")
        elif p[0] == "Summer" and not allow_summer:
            use = "Fall and Spring" if system == "semester" else "Fall, Winter and Spring"
            problems.append(f"{t} is a summer {word}, but summer {word}s are off. Use {use} only.")
        elif last is not None and term_index(*p) > last:
            problems.append(f"{t} is after {term_label(last)}, the last {word} allowed before the transfer goal "
                            f"({goal_term.strip()}). Finish every course by {term_label(last)}.")
    return problems