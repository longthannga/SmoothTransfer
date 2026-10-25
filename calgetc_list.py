"""Foothill's official Cal-GETC course list: which courses count for which Cal-GETC area.

The list is a public PDF (published through ASSIST). Its text is regular, so plain code reads it. No AI
is involved and nothing is guessed: a course is only placed in an area the list names for it.

Pieces:
  parse_list(text)          the PDF text -> {"year", "generated", "courses": {key: entry}}
  load_list(url)            download (Bright Data if configured), read, cache, fall back to the last copy
  areas_for(...)            the areas a course counts for, checked against the term it was taken
  suggest_placements(...)   put a student's courses into the areas that still need one
  lab_keys(...)             which placed science courses also carry the lab (5C)
"""
import hashlib
import json
import os
import re
import time

import ap
import store
from cal_getc import CAL_GETC_AREAS
from terms import SEASONS, term_index
from transcript_match import assist_key, transcript_key

DEFAULT_URL = "https://fhweb.foothill.edu/articulation/pdf/25-26-FH-calgetc-course-list.pdf"
CACHE_DAYS = 7
MIN_COURSES = 100          # a real list has hundreds; fewer means the layout changed and we should not trust it

_AREA = r"[1-6][A-C]?"
_TERM = r"(?:F|W|Sp|Su)\d{4}"
_CODE = r"[A-Z]{1,6}(?: [A-Z]{1,2})? [A-Z]?\d+[A-Z]*"
COMPLETE = re.compile(rf"^(?P<code>{_CODE})\s+(?P<title>.+?)\s+(?P<units>\d+\.\d{{2}})\s+(?P<area>{_AREA})\s+"
                      rf"(?P<appr>{_TERM})(?:\s+(?P<rem>{_TERM}))?\s*$")
START = re.compile(rf"^(?P<code>{_CODE})\s+(?P<title>\S.*)$")
FINISH = re.compile(rf"^(?P<units>\d+\.\d{{2}})\s+(?P<area>{_AREA})\s+(?P<appr>{_TERM})(?:\s+(?P<rem>{_TERM}))?\s*$")
EXTRA = re.compile(rf"^(?P<area>{_AREA})\s+(?P<appr>{_TERM})(?:\s+(?P<rem>{_TERM}))?\s*$")
SAME = re.compile(rf"^Same as:\s*(?P<codes>[A-Z0-9 ,]+?)(?:\s+(?P<area>{_AREA})\s+(?P<appr>{_TERM})(?:\s+(?P<rem>{_TERM}))?)?\s*$")
_SEASON = {"F": "Fall", "W": "Winter", "Sp": "Spring", "Su": "Summer"}


def course_key(code):
    """One spelling for a course, whether it comes from the list ('MATH 1B'), ASSIST ('C S 3B') or a
    Foothill transcript ('MATH F001B')."""
    return transcript_key(code)[0] or assist_key(code)


def tindex(token):
    """'F2025' -> a number that grows by one each quarter (see terms.py). None if it is not a term token."""
    m = re.match(r"^(F|W|Sp|Su)(\d{4})$", token or "")
    return term_index(_SEASON[m.group(1)], int(m.group(2))) if m else None


def term_from_text(text):
    """'2026 Winter Foothill' or 'Winter 2026' -> the same kind of number. None when the text has no term."""
    t = str(text or "")
    m = re.search(r"(\d{4})\s+(Fall|Winter|Spring|Summer)", t, re.I) or None
    if m:
        return term_index(m.group(2).capitalize(), int(m.group(1)))
    m = re.search(r"(Fall|Winter|Spring|Summer)\s+(\d{4})", t, re.I)
    return term_index(m.group(1).capitalize(), int(m.group(2))) if m else None


# ---------- 1. read the list ----------

def parse_list(text):
    """The PDF's text -> {"year", "generated", "courses": {key: {code, title, units, areas, same_as}}}.

    Each course has one or more areas, each with the term it was approved and (rarely) the term it was removed.
    A title can wrap onto the next line, and a second area is written on its own line under the course."""
    year = re.search(r"Academic Year\s+(\d{4}-\d{4})", text)
    generated = re.search(r"Date Generated:\s*([\d/]+)", text)
    courses, last, pending = {}, None, None

    def add_area(entry, area, appr, rem):
        if entry is not None and not any(a["area"] == area and a["approved"] == appr for a in entry["areas"]):
            entry["areas"].append({"area": area, "approved": appr, "removed": rem or ""})

    def finish(code, title, units, area, appr, rem):
        nonlocal last
        key = course_key(code)
        entry = courses.setdefault(key, {"code": code, "title": title.strip(), "units": float(units),
                                         "areas": [], "same_as": []})
        add_area(entry, area, appr, rem)
        last = entry

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = COMPLETE.match(line)
        if m:
            pending = None
            finish(m["code"], m["title"], m["units"], m["area"], m["appr"], m["rem"])
            continue
        m = FINISH.match(line)
        if m and pending:
            finish(pending["code"], pending["title"], m["units"], m["area"], m["appr"], m["rem"])
            pending = None
            continue
        m = SAME.match(line)
        if m and last is not None:
            last["same_as"] += [c.strip() for c in m["codes"].split(",") if c.strip()]
            if m["area"]:
                add_area(last, m["area"], m["appr"], m["rem"])
            continue
        m = EXTRA.match(line)
        if m and last is not None:
            add_area(last, m["area"], m["appr"], m["rem"])
            continue
        m = START.match(line)
        if m:
            pending = {"code": m["code"], "title": m["title"]}
            continue
        if pending:                                   # the rest of a wrapped title
            pending["title"] += " " + line
    return {"year": year.group(1) if year else "", "generated": generated.group(1) if generated else "",
            "courses": courses}


# ---------- 2. download, read, cache ----------

def _db():
    store.db.execute("CREATE TABLE IF NOT EXISTS calgetc_lists(url TEXT PRIMARY KEY, sha256 TEXT, "
                     "fetched_at REAL, via TEXT, data TEXT)")
    return store.db


def _cached(url, max_age_days=CACHE_DAYS):
    row = _db().execute("SELECT * FROM calgetc_lists WHERE url=?", (url,)).fetchone()
    if row and time.time() - row["fetched_at"] < max_age_days * 86400:
        return {"status": "cached", "listing": json.loads(row["data"]), "url": url,
                "fetched_at": row["fetched_at"], "via": row["via"]}
    return None


def load_list(url=None, force_live=False):
    """Download and read the list. Returns {"status", "listing", "url", "via", "fetched_at", "message"}.

    status: ok | cached | stale (the last saved copy, because the download failed) | fetch_failed |
    unsafe_link | unreadable. Nothing here raises."""
    url = (url or os.getenv("CALGETC_LIST_URL") or DEFAULT_URL).strip()
    if not ap.safe_pdf_url(url):
        return {"status": "unsafe_link", "url": url,
                "message": "That link is not a plain https PDF link on a .edu site, so it was not opened."}
    if not force_live:
        hit = _cached(url)
        if hit:
            return hit
    try:
        pdf, via = ap.fetch_pdf(url)
    except ap.FetchError as e:
        old = _cached(url, max_age_days=10 ** 6)
        if old:
            old["status"] = "stale"
            old["message"] = f"Could not download the list right now ({e}). Using the last saved copy."
            return old
        return {"status": "fetch_failed", "url": url, "message": f"Could not download the list: {e}"}
    listing = parse_list(ap.pdf_text(pdf))
    if len(listing["courses"]) < MIN_COURSES:
        return {"status": "unreadable", "url": url,
                "message": f"The list was downloaded but only {len(listing['courses'])} courses could be read, "
                           "so the layout may have changed. Nothing was placed from it."}
    _db().execute("INSERT OR REPLACE INTO calgetc_lists(url, sha256, fetched_at, via, data) VALUES (?,?,?,?,?)",
                  (url, hashlib.sha256(pdf).hexdigest(), time.time(), via, json.dumps(listing)))
    _db().commit()
    return {"status": "ok", "listing": listing, "url": url, "fetched_at": time.time(), "via": via}


def valid_through(listing):
    """The last quarter the list covers: Summer of its academic year (for '2025-2026', Summer 2026)."""
    m = re.match(r"(\d{4})-(\d{4})", (listing or {}).get("year", ""))
    return term_index("Summer", int(m.group(2))) if m else None


# ---------- 3. which areas does a course count for? ----------

def entry_for(listing, code):
    return ((listing or {}).get("courses") or {}).get(course_key(code))


def areas_for(listing, code, term_text=""):
    """The Cal-GETC areas this course counts for, in list order.

    A course only counts if it was approved when it was taken, so when the term is known the areas
    are limited to those approved by then and not yet removed.
    Returns {"areas": [...], "status": ok | not_listed | not_approved_then, "entry": ..., "term_known": bool}."""
    e = entry_for(listing, code)
    if not e:
        return {"areas": [], "status": "not_listed", "entry": None, "term_known": False}
    t = term_from_text(term_text)
    ok = []
    for a in e["areas"]:
        if t is not None:
            start, end = tindex(a["approved"]), tindex(a["removed"])
            if (start is not None and t < start) or (end is not None and t >= end):
                continue
        if a["area"] not in ok:
            ok.append(a["area"])
    return {"areas": ok, "status": "ok" if ok else "not_approved_then", "entry": e, "term_known": t is not None}


def _discipline(code):
    m = re.match(r"^([A-Z]+(?: [A-Z]{1,2})?) [A-Z]?\d", code.strip().upper())
    return m.group(1) if m else code.strip().upper()


_RANK = {"done": 0, "in_progress": 1, "planned": 2}


def suggest_placements(courses, saved, listing):
    """Put courses into the Cal-GETC areas that still need one, using only areas the official list names.

    courses: dicts with key, code, state (done | in_progress | planned) and term.
    saved:   {course key: area} the student already has. These are kept and count toward each area's need.
    A course counts for one area only, so this is a matching problem: finished courses come first, and a
    course approved for several areas goes where it is needed most. Area 4 takes courses from different
    disciplines. A lab-only course (5C) is placed when no placed science course already carries the lab.

    Returns {"placements": {key: area}, "notes": {key: text}, "skipped": {key: text}}."""
    have = {a["id"]: 0 for a in CAL_GETC_AREAS}
    taken_disciplines = set()
    by_key = {c["key"]: c for c in courses}
    for k, a in saved.items():
        if a in have and k in by_key:
            have[a] += 1
            if a == "4":
                taken_disciplines.add(_discipline(by_key[k]["code"]))
    need = {a["id"]: max(int(a["courses"]) - have[a["id"]], 0) for a in CAL_GETC_AREAS if a["id"] != "5C"}

    todo = [c for c in courses if c["key"] not in saved]
    todo.sort(key=lambda c: (_RANK.get(c["state"], 3), term_from_text(c.get("term")) or 0, c["key"]))
    cand, skipped, lab_only = {}, {}, []
    seen4 = set(taken_disciplines)
    for c in todo:
        info = areas_for(listing, c["code"], c.get("term", ""))
        if info["status"] == "not_approved_then":
            skipped[c["key"]] = "It is on the list, but not for the quarter it was taken."
            continue
        areas = info["areas"]
        if areas == ["5C"]:
            lab_only.append(c)
            continue
        areas = [a for a in areas if a != "5C" and need.get(a, 0) > 0]
        if "4" in areas:                               # one course per discipline in Area 4
            d = _discipline(c["code"])
            if d in seen4:
                areas.remove("4")
            else:
                seen4.add(d)
        if areas:
            cand[c["key"]] = areas

    supply = {}
    for areas in cand.values():
        for a in areas:
            supply[a] = supply.get(a, 0) + 1
    order = {a["id"]: i for i, a in enumerate(CAL_GETC_AREAS)}
    slots = {k: [(a, i) for a in sorted(v, key=lambda x: (supply[x], order[x])) for i in range(need[a])]
             for k, v in cand.items()}
    owner = {}

    def assign(k, seen):
        for s in slots[k]:
            if s in seen:
                continue
            seen.add(s)
            if s not in owner or assign(owner[s], seen):
                owner[s] = k
                return True
        return False

    for c in todo:
        if c["key"] in slots:
            assign(c["key"], set())
    placements = {k: s[0] for s, k in owner.items()}

    notes = {}
    for k, area in placements.items():
        c = by_key[k]
        info = areas_for(listing, c["code"], c.get("term", ""))
        text = f"On Foothill's list for Area {area}"
        others = [a for a in info["areas"] if a not in (area, "5C")]
        if others:
            text += f" (also approved for {', '.join(others)}, but it is needed here)"
        if area in ("5A", "5B") and "5C" in info["areas"]:
            text += "; it carries the lab (5C)"
        notes[k] = text
    for c in todo:
        k = c["key"]
        if k in placements or k in skipped:
            continue
        info = areas_for(listing, c["code"], c.get("term", ""))
        if info["status"] == "not_listed":
            skipped[k] = "Not on Foothill's Cal-GETC list."
        elif k in cand or info["areas"]:
            skipped[k] = f"On the list (Area {', '.join(a for a in info['areas'] if a != '5C') or '5C'}), " \
                         "but those areas already have enough courses."

    merged = {**saved, **placements}
    if not any(merged.get(c["key"]) == "5C" for c in courses) and not lab_keys(courses, merged, listing):
        for c in lab_only:                             # a separate lab course, like ANTH 1L
            placements[c["key"]] = "5C"
            notes[c["key"]] = "On Foothill's list for Area 5C (the lab)"
            break
    for c in lab_only:
        if c["key"] not in placements:
            skipped[c["key"]] = "A lab course (5C), and the lab is already covered."
    return {"placements": placements, "notes": notes, "skipped": skipped}


def lab_keys(courses, placements, listing):
    """Keys of placed 5A/5B science courses whose list entry also carries the lab (5C)."""
    out = set()
    for c in courses:
        if placements.get(c["key"]) in ("5A", "5B"):
            info = areas_for(listing, c["code"], c.get("term", ""))
            if "5C" in info["areas"]:
                out.add(c["key"])
    return out
