"""ASSIST "No Course Articulated" handling. Run with:  python -m unittest test_articulation.py"""
import json
import os
import unittest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BROWSER_WS", "wss://test")

import planner
from planner import not_schedulable, finish_plan
from requirements_parser import parse_requirements, group_sections


def cell(cid, prefix, number, title="x"):
    return {"type": "Course", "id": cid, "course": {"prefix": prefix, "courseNumber": number,
                                                    "courseTitle": title, "minUnits": 4.0}}


def articulation(cid, sending=None, reason=None):
    items = [{"position": 0, "items": [{"prefix": p, "courseNumber": n, "minUnits": 6.0} for p, n in (sending or [])]}]
    return {"templateCellId": cid, "articulation": {"sendingArticulation": {
        "noArticulationReason": reason, "items": [] if reason else items}}}


def agreement(sections, articulations):
    """sections: list of (advisement or None, [cells])"""
    secs = [{"advisements": [adv] if adv else [], "rows": [{"cells": [c]} for c in cells]} for adv, cells in sections]
    return {"templateAssets": json.dumps([{"type": "RequirementGroup", "sections": secs}]),
            "articulations": json.dumps(articulations)}


def pick(n, unit="Course"):
    return {"type": "NFollowing", "amount": float(n), "amountUnitType": unit, "selectionType": "Select"}


# the shape of the real UCSD science section: PHYS 2A/2B articulate, PHYS 4A/4B show "No Course Articulated"
SCIENCE = agreement(
    [(pick(1), [cell("a", "CHEM", "6A"), cell("b", "PHYS", "2A"), cell("c", "PHYS", "4A"), cell("d", "PHYS", "4B")]),
     (None, [cell("e", "CSE", "21"), cell("f", "CSE", "29"), cell("g", "ECE", "45")])],
    [articulation("a", [("CHEM", "1A")]), articulation("b", [("PHYS", "4A")]),
     articulation("c", reason="No Course Articulated"),
     # "d", "e" and "f" have no entry at all in the data. ASSIST's page shows those as "No Course Articulated" too.
     articulation("g", reason="This course must be taken at the university after transfer")])


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.reqs = {r["course"]: r for r in parse_requirements(SCIENCE)}

    def test_a_cell_with_no_entry_is_no_course_articulated(self):
        for code in ("PHYS 4B", "CSE 21", "CSE 29"):
            self.assertEqual((self.reqs[code]["status"], self.reqs[code]["note"]), ("none", "No Course Articulated"), code)

    def test_other_statuses_are_unchanged(self):
        self.assertEqual(self.reqs["PHYS 4A"]["status"], "none")
        self.assertEqual(self.reqs["ECE 45"]["status"], "after_transfer")
        self.assertEqual(self.reqs["CHEM 6A"]["status"], "articulated")
        self.assertEqual(self.reqs["PHYS 2A"]["options"], [[{"code": "PHYS 4A", "units": 6.0}]])

    def test_nothing_is_unknown_any_more(self):
        self.assertFalse([r for r in self.reqs.values() if r["status"] == "unknown"])

    def test_rule_text_names_the_unit(self):
        self.assertEqual(self.reqs["CHEM 6A"]["section_rule"], "Select 1 course from the following")
        self.assertEqual(self.reqs["CSE 21"]["section_rule"], "Complete all of the following")
        series = {r["course"]: r for r in parse_requirements(agreement([(pick(1, "Series"), [cell("a", "X", "1")])], []))}
        self.assertEqual(series["X 1"]["section_rule"], "Select 1 series from the following")
        two = {r["course"]: r for r in parse_requirements(agreement([(pick(2), [cell("a", "X", "1")])], []))}
        self.assertEqual(two["X 1"]["section_rule"], "Select 2 courses from the following")
        self.assertEqual(two["X 1"]["pick_unit"], "course")


class NotSchedulableTests(unittest.TestCase):
    def setUp(self):
        self.reqs = parse_requirements(SCIENCE)

    def test_optional_alternatives_are_left_out(self):
        listed = [i["course"] for i in not_schedulable(self.reqs)]
        self.assertNotIn("PHYS 4A", listed)          # the science section can be finished without them
        self.assertNotIn("PHYS 4B", listed)

    def test_required_courses_with_no_foothill_course_are_listed(self):
        listed = {i["course"]: i for i in not_schedulable(self.reqs)}
        self.assertEqual(set(listed), {"CSE 21", "CSE 29", "ECE 45"})
        self.assertEqual(listed["CSE 21"]["status"], "none")
        self.assertEqual(listed["ECE 45"]["status"], "after_transfer")

    def test_a_pick_section_with_too_few_doable_courses_lists_the_rest(self):
        reqs = parse_requirements(agreement(
            [(pick(2), [cell("a", "A", "1"), cell("b", "B", "1"), cell("c", "C", "1")])],
            [articulation("a", [("A", "1")])]))
        self.assertEqual({i["course"] for i in not_schedulable(reqs)}, {"B 1", "C 1"})   # only 1 doable, 2 needed

    def test_ap_covered_courses_are_not_listed(self):
        reqs = [dict(r, ap_met=True) if r["course"] == "CSE 21" else r for r in self.reqs]
        self.assertNotIn("CSE 21", [i["course"] for i in not_schedulable(reqs)])

    def test_group_sections_still_counts_correctly(self):
        science = next(g for g in group_sections(self.reqs) if g["items"][0]["course"] == "CHEM 6A")
        self.assertEqual((science["pick"], science["required"]), (1, 1))


class PlanListTests(unittest.TestCase):
    def test_python_fills_the_cannot_do_list(self):
        reqs = parse_requirements(SCIENCE)
        plan = {"quarters": [], "cannot_do_at_foothill": [{"course": "invented", "note": "x"}],
                "needs_check": [{"course": "PHYS 4A", "note": "unknown status"}]}
        finish_plan(plan, reqs)
        self.assertEqual({i["course"] for i in plan["cannot_do_at_foothill"]}, {"CSE 21", "CSE 29", "ECE 45"})
        self.assertEqual(plan["needs_check"], [])                      # nothing is "unknown" now
        notes = {i["course"]: i["note"] for i in plan["cannot_do_at_foothill"]}
        self.assertIn("No Course Articulated", notes["CSE 21"])
        self.assertIn("after transfer", notes["ECE 45"])

    def test_claude_is_told_to_leave_the_lists_empty(self):
        self.assertIn("program fills those in", planner.SYSTEM)
        self.assertNotIn("have unknown status", planner.SYSTEM)

    def test_build_plan_overrides_whatever_claude_wrote(self):
        reqs = parse_requirements(SCIENCE)
        old = planner._call

        def fake_call(system, messages):
            payload = json.loads(messages[0]["content"])
            listed = {i["course"] for i in payload["cannot_do_at_foothill_or_unknown"]}
            assert "PHYS 4A" not in listed and "CSE 21" in listed
            quarters = [{"term": "Q1", "total_units": 5, "workload": "light", "workload_note": "ok",
                         "courses": [{"code": c["code"], "satisfies": "x", "units": c["units"], "why": "w",
                                      "prereq_assumed": False} for c in payload["courses_to_schedule"]]}]
            return json.dumps({"summary": "s", "quarters": quarters, "warnings": [],
                               "cannot_do_at_foothill": [{"course": "PHYS 4A", "note": "unknown"}],
                               "needs_check": [{"course": "PHYS 4B", "note": "unknown"}]})
        planner._call = fake_call
        try:
            res = planner.build_plan("CS", "UCSD", reqs, set(), 30, "W", "F", "")
        finally:
            planner._call = old
        self.assertEqual({i["course"] for i in res["plan"]["cannot_do_at_foothill"]}, {"CSE 21", "CSE 29", "ECE 45"})
        self.assertEqual(res["plan"]["needs_check"], [])


if __name__ == "__main__":
    unittest.main()
