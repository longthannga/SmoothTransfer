"""Matches saved transcript courses to the Foothill course codes ASSIST uses.

Transcript:  CS F003B   MATH F022.   ANTH F001.   ENGL C1000H   MATH D001A
ASSIST:      C S 3B     MATH 22      ANTH 1       ENGL C1000H   (Foothill only)

Both sides are reduced to the same plain key (letters and digits only), so the
difference in spacing, the "F" campus letter, leading zeros and trailing dots
does not matter. Python decides this, not Claude, because it must be exact.
"""
import re

PASSING = {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "P", "CR"}
BELOW_C = {"C-", "D+", "D", "D-"}  # not counted automatically: majors usually need a C or better
# anything else with a grade (F, NP, NC, W, I) did not pass


def assist_key(code):
    """'C S 3B' -> 'CS3B'"""
    return re.sub(r"[^A-Z0-9]", "", code.upper())


def transcript_key(code):
    """Return (key, campus). campus is 'foothill', 'deanza', or None.
    'CS F003B' -> ('CS3B', 'foothill'); 'MATH D001A' -> ('MATH1A', 'deanza')."""
    m = re.match(r"^\s*(.*?)\s+([A-Z]?)(\d+)([A-Z]*)\.?\s*$", code.upper())
    if not m:
        return None, None
    subject, letter, digits, suffix = m.groups()
    subject = re.sub(r"\s+", "", subject)
    campus = {"F": "foothill", "D": "deanza"}.get(letter)
    keep = "" if campus else letter  # "C" in C1000 is part of the number
    return f"{subject}{keep}{int(digits)}{suffix}", campus


def parse_note(notes):
    """The app stores transcript details in the notes text:
    'Extracted grade: A; record_type: course; parser: simple_parser'."""
    grade = re.search(r"Extracted grade:\s*([^;]*);", notes or "")
    kind = re.search(r"record_type:\s*(\w+)", notes or "")
    return (grade.group(1).strip().upper() if grade else ""), (kind.group(1) if kind else "course")


def classify_saved_courses(saved_courses, assist_codes):
    """Sort saved course records against the ASSIST course codes for this major.

    Returns a dict:
      completed    ASSIST codes the student passed (also AP/transfer credit Foothill accepted)
      in_progress  ASSIST codes still in progress
      below_c      [(transcript code, grade)] matched, but grade is below C
      not_passed   ASSIST codes where every attempt failed or was withdrawn
      deanza       transcript codes from De Anza (a different college in ASSIST)
      other        transcript codes that are not in this major's requirements
      matches      [{"transcript", "assist", "grade", "kind"}] for display
    """
    by_key = {assist_key(c): c for c in assist_codes}
    out = {"completed": set(), "in_progress": set(), "below_c": [], "not_passed": set(),
           "deanza": [], "other": [], "matches": []}
    low, failed, ip = {}, set(), set()

    for c in saved_courses:
        raw = c["course_code"]
        key, campus = transcript_key(raw)
        grade, kind = parse_note(c.get("notes", ""))
        manual = c.get("source") != "transcript_pdf"

        if manual:  # typed in by the student
            school = (c.get("institution") or "").lower()
            if c.get("status") == "Unmapped" or (school and "foothill" not in school):
                out["other"].append(raw)
                continue
            key = key or assist_key(raw)
            campus = campus or "foothill"
            grade = grade or "P"  # the student says it is done

        if key is None:
            out["other"].append(raw)
            continue
        if campus == "deanza":
            out["deanza"].append(raw)
            continue
        target = by_key.get(key)
        if not target:
            out["other"].append(raw)
            continue

        row = {"transcript": raw, "assist": target, "grade": grade or "?", "kind": kind}
        out["matches"].append(row)
        if kind == "in_progress" or grade == "IP":
            ip.add(target)
        elif grade in PASSING:
            out["completed"].add(target)
        elif grade in BELOW_C:
            low[target] = (raw, grade)
        else:
            failed.add(target)

    out["in_progress"] = sorted(ip - out["completed"])
    out["below_c"] = [v for k, v in low.items() if k not in out["completed"]]
    out["not_passed"] = sorted(failed - out["completed"] - set(low))
    out["completed"] = sorted(out["completed"])
    return out