"""Reads ASSIST's Cal-GETC course list (the transferability API) into the SAME `listing` that calgetc_list.py
builds from Foothill's PDF, so the Cal-GETC board, areas_for() and suggest_placements() do not change.

It works the way requirements_parser.py does: ASSIST uses one course object everywhere ("prefix",
"courseNumber", "courseTitle", "minUnits"), so we find those objects wherever they sit in the response and
read the Cal-GETC area (and, when ASSIST gives them, the approved / removed terms) from around them.

Nothing is guessed. An area counts only if it is exactly one of the 11 Cal-GETC areas. If the response does
not hold enough courses with a recognizable area, we say so and fall back to the PDF reader.

  parse_api(data)      raw ASSIST JSON -> listing (same shape as calgetc_list.parse_list)
  load_api_list(...)   fetch through Bright Data, parse, cache a week, fall back to the last copy
  load_best(...)       the API list first, then the Foothill PDF if the API list is not available
"""
import hashlib
import json
import re
import time
from pathlib import Path

import calgetc_list as gcl
from cal_getc import CAL_GETC_AREAS

AREA_IDS = {a["id"] for a in CAL_GETC_AREAS}
_HINT = re.compile(r"area|transfer|getc|requirement|attribute", re.I)   # keys that may hold an area
_AREA_KEYS = ("areaCode", "area", "transferAreaCode", "areaId", "code")
_AREA_VALUE = re.compile(r"^\s*(?:cal[\s-]*getc[\s:_-]*)?(?:area[\s:_-]*)?([1-6][A-C]?)(?![0-9A-Za-z])", re.I)
_TERM = re.compile(r"^(?:F|W|Sp|Su)\d{4}$")


def _area_from(d):
    """The Cal-GETC area a dict names, or None. Only exact area ids count ('1A', '2', '5C' ...)."""
    for k in _AREA_KEYS:
        v = d.get(k)
        if isinstance(v, str):
            m = _AREA_VALUE.match(v)
            if m and m.group(1).upper() in AREA_IDS:
                return m.group(1).upper()
    return None


def _terms(d):
    """(approved, removed) term tokens like 'F2023', taken from the keys' names. Empty when not given."""
    approved = removed = ""
    for k, v in d.items():
        if isinstance(v, str) and _TERM.match(v.strip()):
            low = k.lower()
            if low.startswith("end") or low.endswith("end") or "remov" in low or "expir" in low:
                removed = v.strip()
            elif any(w in low for w in ("begin", "start", "approv", "effective")) or low.startswith("from"):
                approved = v.strip()
    return approved, removed


def _is_course(d):
    return isinstance(d.get("prefix"), str) and d.get("courseNumber") not in (None, "")


def _code(d):
    return f'{d["prefix"]} {d["courseNumber"]}'.strip()


def _own_areas(course):
    """Areas written inside the course object itself: (area, approved, removed) tuples."""
    out = []

    def walk(x, hinted):
        if isinstance(x, dict):
            a = _area_from(x) if hinted else None
            if a:
                out.append((a, *_terms(x)))
            for k, v in x.items():
                if isinstance(v, (dict, list)):
                    walk(v, hinted or bool(_HINT.search(k)))
        elif isinstance(x, list):
            for v in x:
                walk(v, hinted)

    for k, v in course.items():
        if re.search("crosslist", k, re.I):
            continue
        if isinstance(v, (dict, list)):
            walk(v, bool(_HINT.search(k)))
        elif isinstance(v, str) and _HINT.search(k):          # a plain field like "calgetcArea": "3B"
            m = _AREA_VALUE.match(v)
            if m and m.group(1).upper() in AREA_IDS:
                out.append((m.group(1).upper(), "", ""))
    return out


def parse_api(data, year_label=""):
    """Raw ASSIST JSON -> {"year", "generated", "courses": {key: entry}, "source", "stats"}.
    An entry is {"code", "title", "units", "areas": [{"area", "approved", "removed"}], "same_as": []}."""
    courses = {}
    stats = {"course_objects": 0, "with_area": 0, "area_entries": 0, "dated": 0}

    def handle(course, inherited):
        stats["course_objects"] += 1
        entries = _own_areas(course) or list(inherited)
        if not entries:
            return
        stats["with_area"] += 1
        code = _code(course)
        entry = courses.setdefault(gcl.course_key(code), {
            "code": code, "title": str(course.get("courseTitle") or "").strip(),
            "units": float(course.get("minUnits") or 0), "areas": [], "same_as": []})
        for area, approved, removed in entries:
            if not any(a["area"] == area and a["approved"] == approved for a in entry["areas"]):
                entry["areas"].append({"area": area, "approved": approved, "removed": removed})
                stats["area_entries"] += 1
                stats["dated"] += 1 if approved else 0
        for k, v in course.items():
            if re.search("crosslist", k, re.I) and isinstance(v, list):
                for c in v:
                    if isinstance(c, dict) and _is_course(c) and _code(c) not in entry["same_as"]:
                        entry["same_as"].append(_code(c))

    def walk(x, inherited):
        if isinstance(x, list):
            for v in x:
                walk(v, inherited)
        elif isinstance(x, dict):
            if _is_course(x):
                handle(x, inherited)
                return
            mine = inherited
            area = _area_from(x)       # an area that lists its courses: {"code": "1A", "courses": [...]}
            if area and any(isinstance(v, list) and re.search("course", k, re.I) for k, v in x.items()):
                mine = inherited + [(area, *_terms(x))]
            for v in x.values():
                if isinstance(v, (dict, list)):
                    walk(v, mine)

    walk(data, [])
    label = year_label
    if not label:
        m = re.search(r'"(20\d\d-20\d\d)"', json.dumps(data)[:400000])
        label = m.group(1) if m else ""
    stats["dated_share"] = round(stats["dated"] / stats["area_entries"], 2) if stats["area_entries"] else 0.0
    return {"year": label, "generated": "", "courses": courses, "source": "assist_api", "stats": stats}


def _year_label(year_id):
    """ASSIST year ids count from 1949 (77 is 2026-2027 in the agreement data). Only used if the response has no year."""
    return f"{1949 + int(year_id)}-{1950 + int(year_id)}"


def _api_url(institution, year):
    return ("https://assist.org/api/transferability/courses"
            f"?institutionId={institution}&academicYearId={year}&listType=CALGETC")


def load_api_list(institution=51, year=77, force_live=False, page_url=None):
    """Same return shape as calgetc_list.load_list:
    {"status", "listing", "url", "via", "fetched_at", "message", "note"}.
    status: ok | cached | stale | fetch_failed | unreadable. Nothing here raises."""
    import assist                                          # imported here so tests need no browser
    url = _api_url(institution, year)
    if not force_live:
        hit = gcl._cached(url)
        if hit:
            hit["note"] = _note(hit["listing"])
            return hit
    out = assist.fetch_calgetc(institution, year, page_url=page_url)
    if out["status"] != "ok":
        reason = {"blocked": f"ASSIST refused the request (code {out.get('code')})",
                  "slow": "ASSIST took too long to answer"}.get(out["status"], out.get("detail") or out["status"])
        old = gcl._cached(url, max_age_days=10 ** 6)
        if old:
            old["status"] = "stale"
            old["message"] = f"Could not load the list from ASSIST right now ({reason}). Using the last saved copy."
            old["note"] = _note(old["listing"])
            return old
        return {"status": "fetch_failed", "url": url, "message": f"Could not load the list from ASSIST: {reason}"}
    listing = parse_api(out["data"], _year_label(year))
    s = listing["stats"]
    if len(listing["courses"]) < gcl.MIN_COURSES:
        Path("dev_api").mkdir(exist_ok=True)               # keep the response so it can be inspected
        Path("dev_api/calgetc_last_response.json").write_text(json.dumps(out["data"]))
        return {"status": "unreadable", "url": url,
                "message": f"ASSIST answered, but only {len(listing['courses'])} of {s['course_objects']} course "
                           "entries had a Cal-GETC area I could recognize, so nothing was placed from it. "
                           "The response was saved to dev_api/calgetc_last_response.json. Run "
                           "debug_assist_api.py --file on it to see the format."}
    db = gcl._db()
    db.execute("INSERT OR REPLACE INTO calgetc_lists(url, sha256, fetched_at, via, data) VALUES (?,?,?,?,?)",
               (url, hashlib.sha256(json.dumps(out["data"]).encode()).hexdigest(), time.time(),
                "ASSIST API (Bright Data)", json.dumps(listing)))
    db.commit()
    return {"status": "ok", "listing": listing, "url": url, "fetched_at": time.time(),
            "via": "ASSIST API (Bright Data)", "note": _note(listing)}


def _note(listing):
    """A warning to show when the list has no approval terms (so the term check cannot run)."""
    s = (listing or {}).get("stats") or {}
    if s.get("area_entries") and s.get("dated_share", 0) < 0.5:
        return ("This list does not say when each course was approved for its area, so courses are not "
                "checked against the quarter they were taken. Confirm older courses with a counselor.")
    return ""


def load_best(url=None, force_live=False, institution=51, year=77, page_url=None):
    """The list the app should use. A PDF link the student typed wins. Otherwise ASSIST's list, and if that
    is not available, Foothill's PDF (the reason is kept in "fallback_reason")."""
    if url:
        return gcl.load_list(url, force_live=force_live)
    api = load_api_list(institution, year, force_live=force_live, page_url=page_url)
    if api["status"] in ("ok", "cached", "stale"):
        return api
    pdf = gcl.load_list(None, force_live=force_live)
    if pdf["status"] in ("ok", "cached", "stale"):
        pdf["fallback_reason"] = api["message"]
        pdf["note"] = "Using Foothill's PDF list because ASSIST's list was not available: " + api["message"]
        return pdf
    pdf["message"] = f"{api['message']} The PDF list also failed: {pdf.get('message', '')}"
    return pdf
