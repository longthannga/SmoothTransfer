"""Turns raw ASSIST data into a clean list of requirements.

Named requirements_parser.py (not parser.py) to avoid clashing with an old
built-in Python module.
"""
import json


def code(c):
    return f'{c["prefix"]} {c["courseNumber"]}'.strip()


def parse_requirements(result):
    """Join the receiving school's required courses (templateAssets) with the
    community college courses that satisfy them (articulations)."""
    template = json.loads(result["templateAssets"])
    arts = {a["templateCellId"]: a["articulation"] for a in json.loads(result["articulations"])}
    reqs = []
    group_no = 0
    for b_i, block in enumerate(template):
        if block.get("type") != "RequirementGroup":
            continue
        group_no += 1
        for s_i, section in enumerate(block["sections"]):
            advisements = section.get("advisements", [])
            # "pick N" rules, for example CSE 30 or ECE 30
            adv = next((a for a in advisements if a.get("type") == "NFollowing"), None)
            pick = adv["amount"] if adv else None
            unit = str((adv or {}).get("amountUnitType") or "Course").lower()   # "course", "series", ..."
            label = f"{group_no}{chr(65 + s_i)}"  # like ASSIST's "3" + "A" (check against assist.org)
            noun = unit if (pick == 1 or unit.endswith("s")) else unit + "s"
            rule = (f"Select {pick:g} {noun} from the following" if pick else "Complete all of the following")
            for row in section["rows"]:
                for cell in row["cells"]:
                    if cell.get("type") != "Course":
                        continue
                    c = cell["course"]
                    art = arts.get(cell["id"])
                    item = {
                        "course": code(c),
                        "title": c["courseTitle"].strip(),
                        "units": c["minUnits"],
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
                    if art:
                        sa = art["sendingArticulation"]
                        if sa["noArticulationReason"]:
                            item["note"] = sa["noArticulationReason"]
                            item["status"] = ("after_transfer" if "after transfer" in item["note"]
                                              else "none")
                        else:
                            item["status"] = "articulated"
                            groups = sorted(sa["items"], key=lambda g: g["position"])
                            item["options"] = [
                                [{"code": code(x), "units": x["minUnits"]} for x in g["items"]]
                                for g in groups
                            ]
                    else:  # ASSIST has no entry for this course. Its page shows that as "No Course Articulated".
                        item["note"] = "No Course Articulated"
                    reqs.append(item)
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