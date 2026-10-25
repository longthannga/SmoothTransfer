"""Print how SmoothTransfer reads each section of an ASSIST agreement, so you can compare it with assist.org.

  python explain_sections.py                      lists the agreements saved in smooth.db
  python explain_sections.py "77/51/to/7/Major/<id>"   explains one of them
  python explain_sections.py path/to/agreement.json    explains a JSON file (the "result" part of ASSIST's response)

For every section it shows the rule ASSIST states ("Select 1 series", "Complete all of the following"), how many
choices SmoothTransfer will schedule, and each course row with the Foothill options found for it. If a rule is worded
in anything other than courses (a series, units), the section is marked so you can check it by hand.
"""
import json
import sys

from requirements_parser import group_sections, parse_requirements


def explain(agreement):
    reqs = parse_requirements(agreement)
    for g in group_sections(reqs):
        flag = "   <-- NOT counted in courses: check on assist.org" if g.get("pick") and g.get("pick_unit") not in (None, "course") else ""
        print(f"\nSection {g['label']}: {g['rule']}{flag}")
        pick = f"{g['pick']:g} {g.get('pick_unit') or ''}".strip() if g["pick"] else "all"
        print(f"  ASSIST pick: {pick} | SmoothTransfer schedules {g['required']:g} of {len(g['items'])} rows")
        for r in g["items"]:
            opts = " OR ".join(" + ".join(c["code"] for c in grp) for grp in r["options"]) or (r["note"] or r["status"])
            print(f"    {r['course']:<12} {r['status']:<14} {opts}")
        for adv in g.get("advisements") or []:
            print(f"  advisement from ASSIST: {json.dumps(adv)}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg and arg.endswith(".json"):
        explain(json.load(open(arg)))
    else:
        import store
        if not arg:
            rows = store.db.execute("SELECT key FROM agreements").fetchall() if "agreements" in [r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")] else []
            print("Saved agreements:" if rows else "No saved agreements yet. Load a major in the app first.")
            for r in rows:
                print("  ", r[0])
        else:
            row = store.db.execute("SELECT data FROM agreements WHERE key=?", (arg,)).fetchone()   # any age
            if row is None:
                sys.exit("That agreement is not saved. Load the major in the app first, or pass a .json file.")
            explain(json.loads(row[0]))
