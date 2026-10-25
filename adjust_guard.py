"""Catches a free-text note that contradicts what the student ticked.
Example: the note says "I failed chem 1A" but CHEM 1A is marked passed."""
import re

FAIL_WORDS = re.compile(
    r"\b(fail\w*|drop\w*|withdr\w+|retak\w+|flunk\w*|didn'?t pass|did not pass)\b", re.I)


def _n(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _prefix(code):
    return re.match(r"^\D*", code).group(0).strip()  # "CHEM 1A" -> "CHEM", "C S 1B" -> "C S"


def conflicting_codes(change, candidates, failed, known_codes=()):
    """Courses the note seems to say were failed/dropped, but that are not marked failed.
    Exact code ("chem 1a") wins. A subject word ("chem") is only used when the note
    names no exact course from that subject."""
    if not FAIL_WORDS.search(change or ""):
        return []
    text, low = _n(change), change.lower()
    named = {_prefix(c) for c in known_codes if _n(c) in text}
    out = []
    for code in candidates:
        if code in failed:
            continue
        p = _prefix(code)
        if _n(code) in text:
            out.append(code)
        elif len(_n(p)) >= 3 and p not in named and re.search(rf"\b{re.escape(p.lower())}", low):
            out.append(code)
    return out


PAST_HINTS = re.compile(r"\b(already|last quarter|previous quarter|took|taken|finished|passed|ended)\b", re.I)


def implies_finished(change):
    """True if the note talks about something that already happened (failing, finishing...)."""
    return bool(FAIL_WORDS.search(change or "") or PAST_HINTS.search(change or ""))


def scheduled_terms_mentioned(change, quarters, known_codes=()):
    """Courses named in the note that are scheduled in the plan -> {code: term}."""
    text = _n(change or "")
    return {c["code"]: q["term"] for q in quarters for c in q["courses"] if _n(c["code"]) in text}