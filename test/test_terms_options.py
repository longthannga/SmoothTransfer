"""Quarter calendar, summers, transfer date, and work hours. Run with:  python -m unittest test_terms_options.py"""
import json
import os
import unittest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BROWSER_WS", "wss://test")

import planner
from terms import (parse_term, term_index, term_label, last_term_allowed, last_term_label, shift_year,
                   term_problems)


class CalendarTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_term("Winter 2027"), ("Winter", 2027))
        self.assertEqual(parse_term("  fall 2028 "), ("Fall", 2028))
        for bad in ("Quarter 2", "Fall", "2027 Fall", "Autumn 2027", "", None, "Fall 28"):
            self.assertIsNone(parse_term(bad), bad)

    def test_calendar_order_and_round_trip(self):
        order = ["Winter 2027", "Spring 2027", "Summer 2027", "Fall 2027", "Winter 2028"]
        idx = [term_index(*parse_term(t)) for t in order]
        self.assertEqual(idx, list(range(idx[0], idx[0] + 5)))
        self.assertEqual([term_label(i) for i in idx], order)

    def test_last_quarter_before_the_transfer(self):
        self.assertEqual(last_term_label("Fall 2028", False), "Spring 2028")     # summer is skipped when off
        self.assertEqual(last_term_label("Fall 2028", True), "Summer 2028")
        self.assertEqual(last_term_label("Winter 2028", False), "Fall 2027")     # the year rolls back
        self.assertEqual(last_term_label("Spring 2028", True), "Winter 2028")
        self.assertEqual(last_term_label("Summer 2028", False), "Spring 2028")
        self.assertIsNone(last_term_label("someday", True))
        self.assertIsNone(last_term_allowed("", False))

    def test_move_the_goal_a_year(self):
        self.assertEqual(shift_year("Fall 2028", 1), "Fall 2029")
        self.assertEqual(shift_year("winter 2027", 2), "Winter 2029")
        self.assertIsNone(shift_year("next year", 1))

    def test_problems(self):
        ok = ["Winter 2027", "Spring 2027", "Fall 2027", "Winter 2028", "Spring 2028"]
        self.assertEqual(term_problems(ok, "Fall 2028", False), [])
        summer = term_problems(["Winter 2027", "Summer 2027"], "Fall 2028", False)
        self.assertEqual(len(summer), 1)
        self.assertIn("summer quarters are off", summer[0])
        self.assertEqual(term_problems(["Winter 2027", "Summer 2027"], "Fall 2028", True), [])
        late = term_problems(["Spring 2028", "Fall 2028"], "Fall 2028", False)
        self.assertEqual(len(late), 1)
        self.assertIn("Fall 2028 is after Spring 2028", late[0])
        self.assertIn("Finish every course by Spring 2028", late[0])

    def test_odd_names_and_missing_goal_are_never_an_error(self):
        self.assertEqual(term_problems(["Quarter 2", "Q1", ""], "Fall 2028", False), [])
        self.assertEqual(term_problems(["Fall 2035"], "", False), [])           # no goal: nothing to compare
        self.assertEqual(term_problems(["Summer 2027"], "", True), [])
        self.assertEqual(len(term_problems(["Summer 2027"], "", False)), 1)      # summer rule still applies


def req(code, foothill):
    return {"course": code, "title": code, "units": 4, "section_id": f"m-{code}", "pick_n_of_section": None,
            "status": "articulated", "note": None, "options": [[{"code": foothill, "units": 5}]]}


def plan_json(terms_by_course):
    quarters = [{"term": t, "total_units": 5 * len(cs), "workload": "light", "workload_note": "ok",
                 "courses": [{"code": c, "satisfies": "x", "units": 5, "why": "w", "prereq_assumed": False} for c in cs]}
                for t, cs in terms_by_course]
    return json.dumps({"summary": "s", "quarters": quarters, "cannot_do_at_foothill": [], "needs_check": [], "warnings": []})


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._call = planner._call
        self.reqs = [req("MATH 20A", "MATH 1A"), req("MATH 20B", "MATH 1B")]
        self.answers = []

        def fake(system, messages):
            self.calls.append((system, messages))
            return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        planner._call = fake

    def tearDown(self):
        planner._call = self._call

    def payload(self, i=0):
        return json.loads(self.calls[i][1][0]["content"])

    def test_claude_is_told_the_last_term_and_the_summer_rule(self):
        self.answers = [plan_json([("Winter 2027", ["MATH 1A"]), ("Spring 2027", ["MATH 1B"])])]
        planner.build_plan("CS", "UCSD", self.reqs, set(), 20, "Winter 2027", "Fall 2028", "")
        p = self.payload()
        self.assertEqual((p["allow_summer_quarters"], p["last_term_allowed"]), (False, "Spring 2028"))
        self.assertNotIn("student_work_hours_per_week", p)
        planner.build_plan("CS", "UCSD", self.reqs, set(), 20, "Winter 2027", "Fall 2028", "", allow_summer=True)
        self.assertEqual(self.payload(1)["last_term_allowed"], "Summer 2028")

    def test_a_summer_quarter_is_caught_and_repaired_when_summers_are_off(self):
        self.answers = [plan_json([("Winter 2027", ["MATH 1A"]), ("Summer 2027", ["MATH 1B"])]),
                        plan_json([("Winter 2027", ["MATH 1A"]), ("Spring 2027", ["MATH 1B"])])]
        res = planner.build_plan("CS", "UCSD", self.reqs, set(), 20, "Winter 2027", "Fall 2028", "")
        self.assertTrue(any("summer quarters are off" in p for p in res["issues_caught"]), res["issues_caught"])
        self.assertEqual(res["issues_remaining"], [])
        self.assertEqual(len(self.calls), 2)

    def test_summers_are_fine_when_allowed(self):
        self.answers = [plan_json([("Winter 2027", ["MATH 1A"]), ("Summer 2027", ["MATH 1B"])])]
        res = planner.build_plan("CS", "UCSD", self.reqs, set(), 20, "Winter 2027", "Fall 2028", "", allow_summer=True)
        self.assertEqual(res["issues_caught"], [])
        self.assertEqual(len(self.calls), 1)

    def test_a_quarter_after_the_transfer_is_caught(self):
        self.answers = [plan_json([("Spring 2028", ["MATH 1A"]), ("Fall 2028", ["MATH 1B"])]),
                        plan_json([("Winter 2028", ["MATH 1A"]), ("Spring 2028", ["MATH 1B"])])]
        res = planner.build_plan("CS", "UCSD", self.reqs, set(), 20, "Winter 2028", "Fall 2028", "")
        self.assertTrue(any("Fall 2028 is after Spring 2028" in p for p in res["issues_caught"]), res["issues_caught"])
        self.assertEqual(res["issues_remaining"], [])

    def test_moving_the_goal_makes_room(self):
        self.answers = [plan_json([("Spring 2028", ["MATH 1A"]), ("Fall 2028", ["MATH 1B"])])]
        res = planner.build_plan("CS", "UCSD", self.reqs, set(), 20, "Winter 2028", "Fall 2029", "")
        self.assertEqual(res["issues_caught"], [])

    def test_adjust_uses_the_new_goal_summer_setting_and_work_hours(self):
        self.answers = [plan_json([("Spring 2028", ["MATH 1A"]), ("Summer 2028", ["MATH 1B"])])]
        old = {"summary": "s", "quarters": [
            {"term": "Spring 2028", "total_units": 5, "courses": [{"code": "MATH 1A", "units": 5}]},
            {"term": "Summer 2028", "total_units": 5, "courses": [{"code": "MATH 1B", "units": 5}]}]}
        res = planner.adjust_plan(old, self.reqs, set(), "I can work 15 hours a week from now on.", 20, "Fall 2028",
                                  allow_summer=True, work_hours=15)
        p = self.payload()
        self.assertEqual((p["allow_summer_quarters"], p["last_term_allowed"], p["student_work_hours_per_week"]),
                         (True, "Summer 2028", 15))
        self.assertEqual(p["transfer_goal"], "Fall 2028")
        self.assertEqual(res["issues_caught"], [])

    def test_the_prompt_lists_choices_instead_of_picking_one(self):
        for text in (planner.SYSTEM, planner.ADJUST_SYSTEM):
            self.assertIn("Do not recommend one fix over the others", text)
            self.assertIn("work fewer hours", text)
            self.assertIn("add summer", text)
            self.assertIn("move the transfer a year later", text)
            self.assertIn("Never call one of them the best", text)
            self.assertIn("Do not use a\n  Summer quarter unless", text)
            self.assertIn("student_work_hours_per_week", text)


if __name__ == "__main__":
    unittest.main()
