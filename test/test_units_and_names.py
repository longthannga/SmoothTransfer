"""Minimum units, running totals, and renaming Cal-GETC placeholders.
Run with:  python -m unittest test_units_and_names.py"""
import json
import os
import unittest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BROWSER_WS", "wss://test")

import planner
import cal_getc_board as gb
from validator import check_plan
from workload import quarter_totals, units_for_codes


def q(term, *units, **kw):
    return {"term": term, "total_units": sum(units),
            "courses": [{"code": f"C{i}", "units": u} for i, u in enumerate(units)], **kw}


class TotalsTests(unittest.TestCase):
    def test_running_total_by_end_of_each_quarter(self):
        rows = quarter_totals([q("W", 10), q("S", 5, 5), q("F", 4.5)], units_before=60)
        self.assertEqual([r["units"] for r in rows], [10, 10, 4.5])
        self.assertEqual([r["total_by_end"] for r in rows], [70, 80, 84.5])
        self.assertEqual([r["weekly_hours"] for r in rows], [40.0, 40.0, 18.0])

    def test_without_a_starting_total(self):
        rows = quarter_totals([q("W", 8), q("S", 8)])
        self.assertEqual([r["total_by_end"] for r in rows], [8, 16])

    def test_minimum_status(self):
        rows = quarter_totals([q("W", 12), q("S", 8), q("F", 6)], min_units=12)
        self.assertEqual([r["status"] for r in rows],
                         ["meets the minimum", "below the minimum", "below the minimum (last quarter)"])

    def test_empty_quarters_and_no_minimum(self):
        rows = quarter_totals([q("W", 8), {"term": "S", "courses": []}], min_units=0)
        self.assertEqual(rows[0]["status"], "")
        self.assertEqual(rows[1]["status"], "no classes")
        self.assertEqual(rows[1]["total_by_end"], 8)              # an empty quarter keeps the running total

    def test_the_last_quarter_with_classes_is_the_light_one_even_if_empty_quarters_follow(self):
        rows = quarter_totals([q("W", 12), q("S", 6), {"term": "F", "courses": []}], min_units=12)
        self.assertEqual(rows[1]["status"], "below the minimum (last quarter)")

    def test_units_for_codes(self):
        reqs = [{"options": [[{"code": "MATH 1A", "units": 5}], [{"code": "MATH 1AH", "units": 5}]]},
                {"options": [[{"code": "MATH 1A", "units": 5}, {"code": "MATH 1B", "units": 5}]]}]
        self.assertEqual(units_for_codes(reqs, ["MATH 1A", "MATH 1B", "UNKNOWN 1"]), 10.0)   # once each
        self.assertEqual(units_for_codes(reqs, []), 0)


class UnitsSoFarTests(unittest.TestCase):
    def rec(self, code, grade, units, kind="course", source="transcript_pdf"):
        return {"course_code": code, "units": units, "title": "", "term": "", "source": source,
                "notes": f"Extracted grade: {grade}; record_type: {kind}; parser: simple_parser" if source == "transcript_pdf" else ""}

    def test_earned_and_in_progress(self):
        earned, ip = gb.units_so_far([
            self.rec("A 1", "A", 4.5), self.rec("B 1", "P", 5, "ap_ib_or_transfer"), self.rec("C 1", "IP", 4, "in_progress"),
            self.rec("D 1", "D", 5), self.rec("E 1", "F", 5), self.rec("F 1", "", 3, source="manual")])
        self.assertEqual(earned, 4.5 + 5 + 3)        # passed, AP/transfer credit, and hand-entered
        self.assertEqual(ip, 4)                       # below C and failed do not count

    def test_a_retake_is_counted_once(self):
        earned, _ = gb.units_so_far([self.rec("A 1", "F", 5), self.rec("A 1", "B", 5)])
        self.assertEqual(earned, 5)

    def test_nothing_saved(self):
        self.assertEqual(gb.units_so_far([]), (0, 0))


class MinimumCheckTests(unittest.TestCase):
    def test_a_light_middle_quarter_is_a_problem(self):
        plan = {"quarters": [q("W", 12), q("S", 8), q("F", 12)]}
        found = check_plan(plan, [], set(), 20, min_units=12)
        self.assertTrue(any("S has 8 units, under the minimum of 12" in p for p in found), found)

    def test_the_last_quarter_with_classes_may_be_lighter(self):
        self.assertFalse([p for p in check_plan({"quarters": [q("W", 12), q("S", 6)]}, [], set(), 20, min_units=12)
                          if "minimum" in p])

    def test_empty_quarters_are_not_flagged(self):
        plan = {"quarters": [q("W", 12), {"term": "S", "courses": [], "total_units": 0}, q("F", 12)]}
        self.assertFalse([p for p in check_plan(plan, [], set(), 20, min_units=12) if "minimum" in p])

    def test_no_minimum_means_no_check(self):
        self.assertFalse([p for p in check_plan({"quarters": [q("W", 3), q("S", 3), q("F", 3)]}, [], set(), 20)
                          if "minimum" in p])


def major_req(code, foothill, units=5):
    return {"course": code, "title": code, "units": 4, "section_id": f"m-{code}", "pick_n_of_section": None,
            "status": "articulated", "note": None, "options": [[{"code": foothill, "units": units}]]}


class PlannerMinimumTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._call = planner._call
        reqs = [major_req("MATH 20A", "MATH 1A"), major_req("MATH 20B", "MATH 1B"), major_req("CSE 12", "C S 2C")]
        self.reqs = reqs

        def plan_text(quarters):
            return json.dumps({"summary": "s", "quarters": quarters, "cannot_do_at_foothill": [], "needs_check": [],
                               "warnings": []})

        def quarter(term, codes):
            return {"term": term, "total_units": 5 * len(codes), "workload": "light", "workload_note": "ok",
                    "courses": [{"code": c, "satisfies": "x", "units": 5, "why": "w", "prereq_assumed": False} for c in codes]}
        self.light_first = plan_text([quarter("Q1", ["MATH 1A"]), quarter("Q2", ["MATH 1B", "C S 2C"])])
        self.fixed = plan_text([quarter("Q1", ["MATH 1A", "MATH 1B"]), quarter("Q2", ["C S 2C"])])

        def fake_call(system, messages):
            self.calls.append((system, messages))
            return self.light_first if len(self.calls) == 1 else self.fixed
        planner._call = fake_call

    def tearDown(self):
        planner._call = self._call

    def test_claude_is_told_the_minimum_and_a_light_quarter_triggers_a_repair(self):
        res = planner.build_plan("CS", "UCSD", self.reqs, set(), 20, "W", "F", "", min_units=10)
        payload = json.loads(self.calls[0][1][0]["content"])
        self.assertEqual(payload["min_units_per_quarter"], 10)
        self.assertIn("min_units_per_quarter", self.calls[0][0])
        self.assertTrue(any("under the minimum of 10" in p for p in res["issues_caught"]), res["issues_caught"])
        self.assertEqual(res["issues_remaining"], [])
        self.assertEqual(len(self.calls), 2)                    # one repair

    def test_no_minimum_no_repair(self):
        res = planner.build_plan("CS", "UCSD", self.reqs, set(), 20, "W", "F", "")
        self.assertEqual(json.loads(self.calls[0][1][0]["content"])["min_units_per_quarter"], 0)
        self.assertFalse([p for p in res["issues_caught"] if "minimum" in p])

    def test_mark_quarters_passed_checks_the_minimum(self):
        plan = {"summary": "s", "quarters": [q("W", 12), q("S", 4), q("F", 12)]}
        res = planner.mark_quarters_passed(plan, [], set(), ["W"], 20, min_units=12)
        self.assertTrue(any("S has 4 units, under the minimum" in p for p in res["issues_remaining"]))


class NamesTests(unittest.TestCase):
    def setUp(self):
        self.slots = gb.missing_slots({}, [])
        self.code = "CAL-GETC 3A #1"

    def test_a_name_replaces_the_placeholder_title(self):
        plain = {r["options"][0][0]["code"]: r for r in gb.placeholder_reqs(self.slots)}
        named = {r["options"][0][0]["code"]: r for r in gb.placeholder_reqs(self.slots, {self.code: " ART F001 "})}
        self.assertEqual(plain[self.code]["title"], "Arts course · Cal-GETC 3A")
        self.assertEqual(named[self.code]["title"], "ART F001 · Cal-GETC 3A")
        self.assertEqual(named[self.code]["generic_title"], "Arts course · Cal-GETC 3A")
        self.assertEqual(named["CAL-GETC 3B #1"]["title"], plain["CAL-GETC 3B #1"]["title"])   # others unchanged

    def test_a_name_does_not_change_what_the_plan_needs(self):
        a = planner.needed_courses(gb.placeholder_reqs(self.slots), set())
        b = planner.needed_courses(gb.placeholder_reqs(self.slots, {self.code: "ART F001"}), set())
        self.assertEqual([(c["code"], c["units"]) for c in a], [(c["code"], c["units"]) for c in b])
        named = next(c for c in b if c["code"] == self.code)
        self.assertEqual(named["title"], "ART F001 · Cal-GETC 3A")
        self.assertTrue(named["placeholder"])

    def test_finish_plan_remembers_the_generic_title_and_area(self):
        reqs = gb.placeholder_reqs(self.slots, {self.code: "ART F001"})
        plan = {"quarters": [{"term": "Q1", "total_units": 5, "courses": [{"code": self.code, "units": 5}]}]}
        planner.finish_plan(plan, reqs)
        c = plan["quarters"][0]["courses"][0]
        self.assertEqual((c["title"], c["generic_title"], c["area_id"]),
                         ("ART F001 · Cal-GETC 3A", "Arts course · Cal-GETC 3A", "3A"))

    def test_apply_names_updates_an_existing_plan_and_can_undo(self):
        reqs = gb.placeholder_reqs(self.slots)
        plan = {"quarters": [{"term": "Q1", "total_units": 5, "courses": [{"code": self.code, "units": 5},
                                                                         {"code": "MATH 1A", "units": 5}]}],
                "past_quarters": [{"term": "P", "courses": [{"code": "CAL-GETC 1B #1", "units": 5, "result": "passed"}]}]}
        planner.finish_plan(plan, reqs)
        planner.finish_plan({"quarters": plan["past_quarters"]}, reqs)
        gb.apply_names(plan, {self.code: "ART F001", "CAL-GETC 1B #1": "ENGL F001B"})
        self.assertEqual(plan["quarters"][0]["courses"][0]["title"], "ART F001 · Cal-GETC 3A")
        self.assertNotIn("title", plan["quarters"][0]["courses"][1])             # a real course is untouched
        self.assertEqual(plan["past_quarters"][0]["courses"][0]["title"], "ENGL F001B · Cal-GETC 1B")
        gb.apply_names(plan, {})                                                  # clearing goes back to the generic title
        self.assertEqual(plan["quarters"][0]["courses"][0]["title"], "Arts course · Cal-GETC 3A")

    def test_apply_names_copes_with_plans_saved_before_names_existed(self):
        plan = {"quarters": [{"term": "Q1", "courses": [{"code": self.code, "placeholder": True, "title": "Arts course · Cal-GETC 3A", "units": 5}]}]}
        gb.apply_names(plan, {self.code: "ART F001"})
        self.assertEqual(plan["quarters"][0]["courses"][0]["title"], "ART F001 · Cal-GETC 3A")
        gb.apply_names(plan, {})
        self.assertEqual(plan["quarters"][0]["courses"][0]["title"], "Arts course · Cal-GETC 3A")

    def test_the_quarter_board_card_shows_the_new_name(self):
        from board import card_label
        c = {"code": self.code, "placeholder": True, "title": "ART F001 · Cal-GETC 3A", "units": 5, "units_label": "4-6"}
        self.assertEqual(card_label(c), "ART F001 · Cal-GETC 3A · 4-6 units")


if __name__ == "__main__":
    unittest.main()
