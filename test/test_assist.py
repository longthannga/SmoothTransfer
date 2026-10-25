"""End-to-end test: live ASSIST data -> clean requirements -> Claude plan -> adjustment."""
import json
import os

from assist import fetch_majors, fetch_agreement
from requirements_parser import parse_requirements
from planner import build_plan, adjust_plan

# DEV ONLY: reuse one saved response so repeated test runs don't spend Bright Data credits.
# Set to False for the real demo. The demo must fetch live data every time.
USE_DEV_CACHE = False
CACHE_FILE = "dev_cache_agreement.json"

MAJOR = "CSE: Computer Science B.S."
CAMPUS = "UC San Diego"


def get_result():
    if USE_DEV_CACHE and os.path.exists(CACHE_FILE):
        print("(using dev cache)")
        with open(CACHE_FILE) as f:
            return json.load(f)
    m = fetch_majors()
    print("majors:", m["status"], len(m["data"] or []))
    if m["status"] != "ok":
        raise SystemExit
    key = next(x["key"] for x in m["data"] if x["label"] == MAJOR)
    out = fetch_agreement(key)
    print("agreement:", out["status"])
    if out["status"] != "ok":
        raise SystemExit
    if USE_DEV_CACHE:
        with open(CACHE_FILE, "w") as f:
            json.dump(out["data"], f)
    return out["data"]


def show(res):
    print("\nIssues the checker caught:", res["issues_caught"] or "none")
    print("Issues remaining:", res["issues_remaining"] or "none")
    plan = res["plan"]
    print("\nSUMMARY:", plan["summary"])
    for q in plan["quarters"]:
        codes = ", ".join(c["code"] for c in q["courses"])
        print(f"- {q['term']}: {codes} | {q['total_units']} units | {q['workload']}")
    for k in ("what_changed", "needs_check", "cannot_do_at_foothill", "warnings"):
        if plan.get(k):
            print(f"\n{k}:")
            for item in plan[k]:
                print("  *", item)


if __name__ == "__main__":
    reqs = parse_requirements(get_result())
    completed = ["MATH 1A", "C S 1B"]

    res = build_plan(MAJOR, CAMPUS, reqs, completed, max_units=16,
                     start_term="Winter 2027", goal_term="Fall 2028",
                     notes="Works part time, about 15 hours a week")
    show(res)

    print("\n=== ADJUSTMENT: the student failed MATH 1A ===")
    completed = ["C S 1B"]  # MATH 1A is no longer completed
    res2 = adjust_plan(res["plan"], reqs, completed,
                       change="I failed MATH 1A. I need to retake it, and I can only take 12 units next quarter.",
                       max_units=12, goal_term="Fall 2028")
    show(res2)