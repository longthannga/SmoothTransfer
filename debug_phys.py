"""Debug helper: shows what ASSIST really sends for UCSD PHYS 4A / 4B.

Run:  python3 debug_phys.py
Uses dev_cache_agreement.json if it exists (free). Otherwise it fetches once
through Bright Data and saves that file for next time. Dev only.
Prints course codes and ids only, nothing private.
"""
import json
import os

from assist import fetch_majors, fetch_agreement

CACHE = "dev_cache_agreement.json"
MAJOR = "CSE: Computer Science B.S."
TARGETS = {"4A", "4B"}


def load_result():
    if os.path.exists(CACHE):
        print(f"(using {CACHE})")
        with open(CACHE) as f:
            return json.load(f)
    m = fetch_majors()
    key = next(x["key"] for x in m["data"] if x["label"] == MAJOR)
    out = fetch_agreement(key)
    print("fetch status:", out["status"])
    if out["status"] != "ok":
        raise SystemExit
    with open(CACHE, "w") as f:
        json.dump(out["data"], f)
    return out["data"]


def code_of(c):
    return f'{c["prefix"]} {c["courseNumber"]}'.strip()


result = load_result()
template = json.loads(result["templateAssets"])
arts = json.loads(result["articulations"])
art_by_id = {a["templateCellId"]: a for a in arts}

# Foothill-side codes that appear in articulations (to test the same-name idea)
foothill = set()
for a in arts:
    for g in a["articulation"].get("sendingArticulation", {}).get("items", []):
        for x in g.get("items", []):
            foothill.add(code_of(x))

all_cell_ids, types = set(), set()
print("\n--- UCSD template cells for PHYS 4A / 4B ---")
for b_i, block in enumerate(template):
    types.add(("block", block.get("type")))
    for s_i, sec in enumerate(block.get("sections", [])):
        for row in sec["rows"]:
            for cell in row["cells"]:
                types.add(("cell", cell.get("type")))
                if cell.get("id"):
                    all_cell_ids.add(cell["id"])
                c = cell.get("course")
                if cell.get("type") == "Course" and c and c["prefix"] == "PHYS" \
                        and c["courseNumber"] in TARGETS:
                    code = code_of(c)
                    hit = art_by_id.get(cell["id"])
                    print(f"\n{code}: block {b_i}, section {s_i}, cell id {cell['id']}")
                    print("  articulation entry with this cell id:", bool(hit))
                    if hit:
                        sa = hit["articulation"].get("sendingArticulation", {})
                        print("  reason:", sa.get("noArticulationReason"),
                              "| Foothill groups:", len(sa.get("items", [])))
                    print("  same code also used on the Foothill side:", code in foothill)
                    print("  section advisements:", sec.get("advisements"))
                    print("  courses in this section:",
                          [code_of(x["course"]) for r in sec["rows"] for x in r["cells"]
                           if x.get("type") == "Course"])

print("\n--- articulation entries whose UCSD course is PHYS 4A / 4B ---")
found = False
for a in arts:
    c = a["articulation"].get("course") or {}
    if c.get("prefix") == "PHYS" and c.get("courseNumber") in TARGETS:
        found = True
        sa = a["articulation"].get("sendingArticulation", {})
        foot = [code_of(x) for g in sa.get("items", []) for x in g.get("items", [])]
        print(f"{code_of(c)} | templateCellId {a['templateCellId']} | "
              f"id found in template: {a['templateCellId'] in all_cell_ids} | "
              f"reason: {sa.get('noArticulationReason')} | Foothill: {foot}")
if not found:
    print("none")

print("\nTypes present:", sorted(types, key=str))