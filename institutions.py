"""What an ASSIST agreement says about the two institutions, and a guard against pairs SmoothTransfer cannot plan for.

SmoothTransfer plans in QUARTERS: the terms, the 4-hours-per-unit rule, the unit limits and the transfer minimum all
assume quarter units. A semester college would give wrong numbers without any warning, so it is refused instead.
"""
import json


def _load(x):
    if isinstance(x, dict):
        return x
    try:
        v = json.loads(x) if isinstance(x, str) else {}
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def _one(inst):
    names = inst.get("names") or []
    name = (names[0].get("name") if names and isinstance(names[0], dict) else "") or str(inst.get("code") or "").strip()
    return {"id": inst.get("id"), "name": name, "term_type": str(inst.get("termType") or "").strip()}


def institution_info(agreement):
    """{"sending": {id, name, term_type}, "receiving": {...}} from an agreement. Missing parts come back empty."""
    a = agreement if isinstance(agreement, dict) else {}
    return {"sending": _one(_load(a.get("sendingInstitution"))), "receiving": _one(_load(a.get("receivingInstitution")))}


def system_of(info):
    """'quarter' or 'semester' for the SENDING college, from an agreement's institution info, or None if it does not say.
    Only the sending college matters for a plan: the courses, units and terms are the community college's.
    (The university's own system only changes how UC counts units, which the transfer minimum already allows for.)"""
    t = ((info or {}).get("sending") or {}).get("term_type", "")
    t = str(t).strip().lower()
    return t if t in ("quarter", "semester") else None


def kind_of(inst):
    """'UC', 'CSU', 'CCC' or '' for an institution's info or a parsed institution."""
    cat = str((inst or {}).get("category", "")).strip().upper()
    return {"UC": "UC", "1": "UC", "CSU": "CSU", "0": "CSU", "CCC": "CCC", "2": "CCC"}.get(cat, "")


def ids_from_key(key):
    """ASSIST's agreement key '77/51/to/7/Major/<id>' -> {"year": 77, "sending": 51, "receiving": 7}, or None."""
    try:
        year, sending, direction, receiving = str(key).split("/")[:4]
        if direction.lower() != "to":
            return None
        return {"year": int(year), "sending": int(sending), "receiving": int(receiving)}
    except (ValueError, TypeError):
        return None


# ---------- the list of colleges and universities (https://assist.org/api/institutions) ----------

TERM_SYSTEM = {0: "semester", 1: "quarter"}   # ASSIST's numbers. UC San Diego is 1 and quarters; most community colleges are 0.
CATEGORY = {0: "CSU", 1: "UC", 2: "CCC"}


def year_start(year_id):
    """ASSIST's academic year id -> the calendar year it begins in (77 is 2026-2027)."""
    return 1949 + int(year_id)


def _latest_name(names):
    best, best_year = "", -1
    for n in names or []:
        if isinstance(n, dict) and n.get("name") and int(n.get("fromYear") or 0) > best_year:
            best, best_year = str(n["name"]).strip(), int(n.get("fromYear") or 0)
    return best


def parse_institutions(raw):
    """ASSIST's institution list -> [{id, name, code, is_cc, category, kind, terms, term_type}], sorted by name.

    is_cc is ASSIST's own isCommunityCollege flag. A community college can be the sending school and a four-year
    university (UC or CSU) the receiving school. `terms` is ASSIST's list of (from year, term type), because a school
    can change system (Cal Poly San Luis Obispo went from quarters to semesters in 2026)."""
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict) or r.get("id") is None:
            continue
        terms = []
        for t in r.get("termTypeAcademicYears") or []:
            if isinstance(t, dict) and t.get("fromYear") is not None and t.get("termType") is not None:
                terms.append((int(t["fromYear"]), int(t["termType"])))
        cat = r.get("category")
        out.append({"id": int(r["id"]), "name": _latest_name(r.get("names")) or str(r.get("code") or "").strip(),
                    "code": str(r.get("code") or "").strip(), "is_cc": bool(r.get("isCommunityCollege")),
                    "category": cat, "kind": CATEGORY.get(cat, "CCC" if r.get("isCommunityCollege") else ""),
                    "terms": terms, "term_type": r.get("termType")})
    return sorted(out, key=lambda x: x["name"].lower())


def system_for_year(inst, academic_year_start):
    """'quarter' or 'semester' for the academic year that begins in `academic_year_start`, or None if unknown."""
    best = None
    for from_year, tt in inst.get("terms") or []:
        if from_year <= academic_year_start and (best is None or from_year > best[0]):
            best = (from_year, tt)
    return TERM_SYSTEM.get(best[1] if best else inst.get("term_type"))


def sending_options(insts):
    """Schools a student can transfer FROM: the community colleges."""
    return [i for i in insts if i["is_cc"]]


def receiving_options(insts):
    """Schools a student can transfer TO: everything that is not a community college (UC and CSU campuses)."""
    return [i for i in insts if not i["is_cc"]]


def describe(inst, year_id):
    s = system_for_year(inst, year_start(year_id))
    return f"{inst['name']}" + (f" · {s}s" if s else "")


# Always available, so the app works before the full list has been loaded.
FALLBACK = parse_institutions([
    {"id": 51, "names": [{"name": "Foothill College"}], "code": "FOOTHILL", "isCommunityCollege": True, "category": 2,
     "termType": 1, "termTypeAcademicYears": [{"termType": 1, "fromYear": 1980}]},
    {"id": 7, "names": [{"name": "University of California, San Diego"}], "code": "UCSD", "isCommunityCollege": False,
     "category": 1, "termType": 1, "termTypeAcademicYears": [{"termType": 1, "fromYear": 1980}]},
])
