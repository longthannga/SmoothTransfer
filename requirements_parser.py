"""Turns raw ASSIST data into a clean list of requirements.

Named requirements_parser.py (not parser.py) to avoid clashing with an old
built-in Python module.
"""
import json

# Notes about anything the last call to parse_requirements could not read (the app shows these).
PARSE_NOTES = []


def code(c):
    return f'{c["prefix"]} {c["courseNumber"]}'.strip()


def parse_requirements(result):
    """Join the receiving school's required courses (templateAssets) with the
    community college courses that satisfy them (articulations)."""
    PARSE_NOTES.clear()
    skipped_sections = skipped_cells = 0
    template = json.loads(result.get("templateAssets") or "[]")
    arts = {a["templateCellId"]: a.get("articulation") for a in json.loads(result.get("articulations") or "[]")
            if isinstance(a, dict) and "templateCellId" in a}
    reqs = []
    group_no = 0
    for b_i, block in enumerate(template):
        if block.get("type") != "RequirementGroup":
            continue
        group_no += 1
        for s_i, section in enumerate(block.get("sections") or []):
            advisements = section.get("advisements", [])
            # "pick N" rules, for example CSE 30 or ECE 30
            adv = next((a for a in advisements if a.get("type") == "NFollowing"), None)
            pick = adv["amount"] if adv else None
            unit = str((adv or {}).get("amountUnitType") or "Course").lower()   # "course", "series", ..."
            label = f"{group_no}{chr(65 + s_i)}"  # like ASSIST's "3" + "A" (check against assist.org)
            noun = unit if (pick == 1 or unit.endswith("s")) else unit + "s"
            rule = (f"Select {pick:g} {noun} from the following" if pick else "Complete all of the following")
            rows = section.get("rows") if isinstance(section, dict) else None
            if not rows:
                skipped_sections += 1   # a section laid out differently from the usual one
                continue
            for row in rows:
                for cell in row.get("cells") or []:
                    if cell.get("type") != "Course":
                        continue
                    c = cell.get("course")
                    if not c or "prefix" not in c or "courseNumber" not in c:
                        skipped_cells += 1
                        continue
                    art = arts.get(cell.get("id"))
                    item = {
                        "course": code(c),
                        "title": (c.get("courseTitle") or "").strip(),
                        "units": c.get("minUnits") or 0,
                        "section_id": f"{b_i}-{s_i}",
                        "pick_n_of_section": pick,
                        "pick_unit": unit if pick else None,
                        "section_label": label,
                        "section_rule": rule,
                        "section_advisements": advisements,  # raw, for checking the rule text
                        "status": "none",      # articulated | none | after_transfer
                        "options": [],          # outer list = either option, inner list = all required together
                        "note": None,
                    }
                    sa = (art or {}).get("sendingArticulation")
                    if art and sa:
                        if sa.get("noArticulationReason"):
                            item["note"] = sa["noArticulationReason"]
                            item["status"] = ("after_transfer" if "after transfer" in item["note"]
                                              else "none")
                        else:
                            item["status"] = "articulated"
                            groups = sorted(sa.get("items") or [], key=lambda g: g.get("position", 0))
                            item["options"] = [
                                [{"code": code(x), "units": x.get("minUnits") or 0} for x in g.get("items") or []]
                                for g in groups
                            ]
                    else:  # ASSIST has no entry for this course. Its page shows that as "No Course Articulated".
                        item["note"] = "No Course Articulated"
                    reqs.append(item)
    if skipped_sections:
        PARSE_NOTES.append(f"{skipped_sections} section(s) of this ASSIST agreement have a layout SmoothTransfer "
                           "cannot read yet, so they are left out. Check them on assist.org.")
    if skipped_cells:
        PARSE_NOTES.append(f"{skipped_cells} course(s) in this agreement were missing details and were left out.")
    return reqs


def remaining(reqs, completed):
    """Split requirements into (done, still_needed). `completed` is a set of
    community college course codes, for example {"MATH 1A", "C S 1B"}."""
    done, todo = [], []
    for r in reqs:
        satisfied = bool(r.get("ap_met")) or any(all(c["code"] in completed for c in group)
                                                 for group in r["options"])
        (done if satisfied else todo).append(r)
    return done, todo

def group_sections(reqs):
    """Group requirements by ASSIST section, in ASSIST's order.
    Each group: id, label, rule (text), pick, items, required (how many must be met)."""
    groups = {}
    for r in reqs:
        g = groups.setdefault(r["section_id"], {
            "id": r["section_id"],
            "label": r.get("section_label", r["section_id"]),
            "rule": r.get("section_rule", ""),
            "pick": r["pick_n_of_section"],
            "pick_unit": r.get("pick_unit"),   # "course", "series", ... as ASSIST words it
            "advisements": r.get("section_advisements", []),
            "items": [],
        })
        g["items"].append(r)
    for g in groups.values():
        doable = [r for r in g["items"] if r["status"] == "articulated"]
        base = min(g["pick"], len(doable)) if g["pick"] else len(doable)
        g["ap_covered"] = sum(1 for r in doable if r.get("ap_met"))  # met by AP credit (see ap.py)
        g["required"] = max(base - g["ap_covered"], 0)
    return list(groups.values())