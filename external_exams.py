"""AP and IB exams that count toward Cal-GETC areas.

The tables below are copied from the Cal-GETC Standards, version 1.3, section 6 "Credit by External Exams":
https://deanza.edu/articulation/documents/Cal-GETC%20Standards%20Version%201.3%20External%20Exams.pdf
Rules from the same section:
  - AP needs a score of 3, 4 or 5. IB needs a score of 5, 6 or 7 on a Higher Level exam.
  - Each exam counts for ONE area. The exception is a physical or biological science AP exam, which counts for
    5A or 5B and also meets the lab (5C). An IB exam does not meet the lab.
  - There is no AP or IB exam for Area 1B, 1C or 6. CLEP and other exams cannot be used.
  - A passing score is worth 4 quarter units for Cal-GETC. AP Biology, Chemistry, Physics 1 and Physics 2 are
    worth 5 quarter units toward Area 5.
  - Someone who counts an exam should not also take a comparable college course: credit is not given for both.
Standards change, so check the version and date above against the current one.
"""
import re

SOURCE_URL = "https://deanza.edu/articulation/documents/Cal-GETC%20Standards%20Version%201.3%20External%20Exams.pdf"
SOURCE_NAME = "Cal-GETC Standards, version 1.3, section 6 (credit by external exams)"
AP_MIN_SCORE = 3
IB_MIN_SCORE = 5

# (exam name, areas, how). how = "one": the exam counts for one of the areas listed.
# how = "all": a science exam that counts for 5A or 5B and ALSO the lab (5C).
AP = [
    ("Art History", ["3A", "3B"], "one"),
    ("Biology", ["5B", "5C"], "all"),
    ("Calculus AB", ["2"], "one"),
    ("Calculus BC", ["2"], "one"),
    ("Calculus BC/AB sub score", ["2"], "one"),
    ("Chemistry", ["5A", "5C"], "all"),
    ("Chinese Language and Culture", ["3B"], "one"),
    ("Comparative Government and Politics", ["4"], "one"),
    ("English Language and Composition", ["1A"], "one"),
    ("English Literature and Composition", ["1A", "3B"], "one"),
    ("Environmental Science", ["5A", "5C"], "all"),
    ("European History", ["3B", "4"], "one"),
    ("French Language and Culture", ["3B"], "one"),
    ("German Language and Culture", ["3B"], "one"),
    ("Human Geography", ["4"], "one"),
    ("Italian Language and Culture", ["3B"], "one"),
    ("Japanese Language and Culture", ["3B"], "one"),
    ("Latin", ["3B"], "one"),
    ("Macroeconomics", ["4"], "one"),
    ("Microeconomics", ["4"], "one"),
    ("Physics 1: Algebra-Based", ["5A", "5C"], "all"),
    ("Physics 2: Algebra-Based", ["5A", "5C"], "all"),
    ("Physics C: Mechanics", ["5A", "5C"], "all"),
    ("Physics C: Electricity and Magnetism", ["5A", "5C"], "all"),
    ("Psychology", ["4"], "one"),
    ("Spanish Language and Culture", ["3B"], "one"),
    ("Spanish Literature and Culture", ["3B"], "one"),
    ("Statistics", ["2"], "one"),
    ("U.S. Government and Politics", ["4"], "one"),
    ("U.S. History", ["3B", "4"], "one"),
    ("World History: Modern", ["3B", "4"], "one"),
]
# Higher Level exams only. The IB does not meet the lab (5C).
IB = [
    ("Biology", ["5B"], "one"),
    ("Chemistry", ["5A"], "one"),
    ("Economics", ["4"], "one"),
    ("Geography", ["4"], "one"),
    ("History (any region)", ["3B", "4"], "one"),
    ("Language A: Literature (any language, except English)", ["3B"], "one"),
    ("Language A: Language and Literature (any language, except English)", ["3B"], "one"),
    ("Mathematics: Analysis and Approaches", ["2"], "one"),
    ("Mathematics: Applications and Interpretation", ["2"], "one"),      # the Standards add: may not be at all UC
    ("Physics", ["5A"], "one"),
    ("Psychology", ["4"], "one"),
    ("Theatre", ["3A"], "one"),
]
# Quarter units an exam is worth for Cal-GETC. 5 for the AP exams the Standards single out, otherwise 4.
FIVE_UNIT_AP = {"Biology", "Chemistry", "Physics 1: Algebra-Based", "Physics 2: Algebra-Based"}

_STOP = {"and", "the", "of", "ap", "ib", "hl", "higher", "level", "exam", "examination", "a"}


def _tokens(name):
    """The words of an exam name that matter, so 'U.S. History' and 'United States History' compare equal."""
    t = str(name or "").lower().replace("&", " and ")
    t = re.sub(r"\([^)]*\)", " ", t)                       # '(any region)', '(AB subscore)' ...
    t = t.replace("u.s.", " us ").replace("united states", " us ")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    words = [w for w in t.split() if w not in _STOP]
    return frozenset(w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words)


def _rows(table, kind):
    return [{"name": n, "areas": a, "how": h, "kind": kind, "tokens": _tokens(n)} for n, a, h in table]


_AP_ROWS, _IB_ROWS = _rows(AP, "AP"), _rows(IB, "IB")


def match_exam(kind, name):
    """The Standards row for an exam name, or None when the exam has no Cal-GETC area (for example
    Computer Science A) or the name is too vague to be sure ('History'). Never guesses between rows."""
    rows = _AP_ROWS if str(kind).upper() == "AP" else _IB_ROWS
    q = _tokens(name)
    if not q:
        return None
    exact = [r for r in rows if r["tokens"] == q]
    if exact:
        same = all((r["areas"], r["how"]) == (exact[0]["areas"], exact[0]["how"]) for r in exact)
        return exact[0] if same else None
    inside = [r for r in rows if q < r["tokens"]]          # 'Spanish Language' is part of 'Spanish Language and Culture'
    return inside[0] if len(inside) == 1 else None


def credit_ok(kind, score):
    """Whether the score is high enough for Cal-GETC credit (AP 3 or more, IB Higher Level 5 or more)."""
    try:
        s = int(float(str(score).strip()))
    except (TypeError, ValueError):
        return False
    return s >= (AP_MIN_SCORE if str(kind).upper() == "AP" else IB_MIN_SCORE)


def exam_units(row):
    return 5.0 if row["kind"] == "AP" and row["name"] in FIVE_UNIT_AP else 4.0


def exam_entries(exams):
    """Board entries for a student's saved exam credits (rows with exam_type, subject, score).

    Each entry carries its own areas, so it needs no course list. Exams with no Cal-GETC area, or a score below
    the minimum, are left out and returned in the second list with the reason."""
    entries, skipped, seen = [], [], set()
    for e in exams or []:
        kind, name, score = str(e.get("exam_type", "")).upper(), str(e.get("subject", "")).strip(), e.get("score")
        key = f"{kind}:{name}"
        if key in seen:
            continue
        seen.add(key)
        row = match_exam(kind, name)
        if row is None:
            skipped.append((f"{kind} {name}", "has no Cal-GETC area in the Standards (or the name is not specific enough)"))
        elif not credit_ok(kind, score):
            need = AP_MIN_SCORE if kind == "AP" else IB_MIN_SCORE
            skipped.append((f"{kind} {name}", f"score {score} is below the {need} needed for Cal-GETC"))
        else:
            units = exam_units(row)
            entries.append({"key": key, "code": f"{kind} {name}", "title": f"{kind} exam, score {score}", "units": units,
                            "grade": "", "kind": "exam", "state": "done", "term": "", "reason": "",
                            "areas": list(row["areas"]), "label": f"{kind} {name} · score {score} · exam"})
    return entries, skipped
