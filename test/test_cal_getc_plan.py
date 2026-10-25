"""Cal-GETC in the plan and the study-hours rule. Run with:  python -m unittest test_cal_getc_plan.py"""
import json
import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BROWSER_WS", "wss://test")

import planner
import store
import cal_getc_board as gb
from cal_getc import CAL_GETC_AREAS
from planner import needed_courses, finish_plan
from settings import get_setting, set_setting
from validator import check_plan
from workload import HOURS_PER_UNIT, quarter_hours

AREA_IDS = [a["id"] for a in CAL_GETC_AREAS]


def course(key, state="done"):
    return {"key": key, "code": key, "title": "", "units": 4.0, "state": state, "label": key}


def major_req(code, foothill, units=5):
    return {"course": code, "title": code, "units": 4, "section_id": f"m-{code}", "pick_n_of_section": None,
            "status": "articulated", "note": None, "options": [[{"code": foothill, "units": units}]]}


class MissingSlotTests(unittest.TestCase):
    def test_nothing_placed_means_everything_is_missing(self):
        slots = gb.missing_slots({}, [])
        by_area = {}
        for s in slots:
            by_area[s["area"]] = by_area.get(s["area"], 0) + 1
        expected = {a["id"]: int(a["courses"]) for a in CAL_GETC_AREAS if a["id"] not in gb.NO_PLACEHOLDER}
        self.assertEqual(by_area, expected)
        self.assertNotIn("5C", by_area)                       # the lab is part of a science course

    def test_placed_courses_remove_slots(self):
        courses = [course("ENGL C1000"), course("ANTH F001."), course("ECON F001A"), course("ARTX F001.", "in_progress")]
        placements = {"ENGL C1000": "1A", "ANTH F001.": "4", "ECON F001A": "4", "ARTX F001.": "3A"}
        areas = {s["area"] for s in gb.missing_slots(placements, courses)}
        self.assertNotIn("1A", areas)
        self.assertNotIn("3A", areas)                         # an in-progress course counts as placed
        four = next(a["courses"] for a in CAL_GETC_AREAS if a["id"] == "4")
        self.assertEqual("4" in areas, four > 2)
        self.assertIn("1B", areas)

    def test_courses_that_no_longer_exist_are_not_counted(self):
        self.assertEqual(len(gb.missing_slots({"GONE 1": "1A"}, [])), len(gb.missing_slots({}, [])))

    def test_extra_courses_in_an_area_do_not_go_negative(self):
        courses = [course("A 1"), course("A 2"), course("A 3")]
        slots = gb.missing_slots({"A 1": "1A", "A 2": "1A", "A 3": "1A"}, courses)
        self.assertNotIn("1A", {s["area"] for s in slots})

    def test_titles_read_like_a_course_you_still_need(self):
        slots = gb.missing_slots({}, [])
        arts = next(s for s in slots if s["area"] == "3A")
        self.assertEqual(gb.slot_title(arts), "Arts course · Cal-GETC 3A")
        four = [s for s in slots if s["area"] == "4"]
        if len(four) > 1:
            self.assertIn("1 of", gb.slot_title(four[0]))


class PlaceholderTests(unittest.TestCase):
    def setUp(self):
        self.slots = gb.missing_slots({}, [])
        self.reqs = gb.placeholder_reqs(self.slots)

    def test_one_requirement_per_slot_with_unique_codes(self):
        codes = [r["options"][0][0]["code"] for r in self.reqs]
        self.assertEqual(len(codes), len(self.slots))
        self.assertEqual(len(set(codes)), len(codes))
        self.assertEqual(len({r["section_id"] for r in self.reqs}), len(self.reqs))

    def test_units(self):
        r = self.reqs[0]
        self.assertEqual((r["units_min"], r["units_max"], r["units"]), (4, 6, gb.PLAN_UNITS))
        self.assertTrue(4 <= gb.PLAN_UNITS <= 6)

    def test_the_planner_schedules_them_with_flags_for_claude(self):
        out = needed_courses(self.reqs + [major_req("MATH 20A", "MATH 1A")], set())
        by = {c["code"]: c for c in out}
        self.assertIn("MATH 1A", by)
        self.assertNotIn("placeholder", by["MATH 1A"])
        ph = [c for c in out if c.get("placeholder")]
        self.assertEqual(len(ph), len(self.slots))
        self.assertEqual(ph[0]["units_range"], "4-6")
        self.assertIn("course", ph[0]["title"])

    def test_the_checker_requires_every_placeholder(self):
        codes = [r["options"][0][0]["code"] for r in self.reqs]
        full = {"quarters": [{"term": "Q1", "total_units": 5 * len(codes),
                              "courses": [{"code": c, "units": 5} for c in codes]}]}
        self.assertEqual(check_plan(full, self.reqs, set(), 999), [])
        missing_one = {"quarters": [{"term": "Q1", "total_units": 5 * (len(codes) - 1),
                                     "courses": [{"code": c, "units": 5} for c in codes[:-1]]}]}
        self.assertTrue(check_plan(missing_one, self.reqs, set(), 999))


class HoursTests(unittest.TestCase):
    def test_rule_is_four_hours_per_unit(self):
        self.assertEqual(HOURS_PER_UNIT, 4)
        self.assertEqual(quarter_hours([{"units": 5}, {"units": 4.5}])["weekly_hours"], 38.0)

    def test_placeholders_can_run_higher(self):
        h = quarter_hours([{"units": 5, "units_max": 6}, {"units": 5}])
        self.assertEqual((h["weekly_hours"], h["weekly_hours_max"], h["total_units_max"]), (40.0, 44.0, 11.0))

    def test_finish_plan_decorates_placeholders_and_adds_hours(self):
        reqs = gb.placeholder_reqs(gb.missing_slots({}, []))
        code = reqs[0]["options"][0][0]["code"]
        plan = {"quarters": [{"term": "Q1", "total_units": 10, "courses": [
            {"code": code, "units": 5, "satisfies": "x"}, {"code": "MATH 1A", "units": 5, "satisfies": "y"}]}]}
        finish_plan(plan, reqs)
        q = plan["quarters"][0]
        ph, real = q["courses"]
        self.assertTrue(ph["placeholder"])
        self.assertEqual(ph["units_label"], "4-6")
        self.assertTrue(ph["satisfies"].startswith("Cal-GETC"))
        self.assertNotIn("placeholder", real)
        self.assertEqual((q["weekly_hours"], q["weekly_hours_max"]), (40.0, 44.0))

    def test_finish_plan_is_safe_to_repeat_and_on_empty_plans(self):
        plan = {"quarters": [{"term": "Q1", "total_units": 0, "courses": []}]}
        finish_plan(finish_plan(plan, []), [])
        self.assertEqual(plan["quarters"][0]["weekly_hours"], 0)
        self.assertEqual(finish_plan({}, []), {})


class ClaudeInputTests(unittest.TestCase):
    """build_plan end to end with Claude's call replaced by a pretend answer."""

    def setUp(self):
        self.seen = {}
        self._call = planner._call

        def fake_call(system, messages):
            payload = json.loads(messages[0]["content"])
            self.seen["system"], self.seen["payload"] = system, payload
            quarters = [{"term": f"Q{i + 1}", "total_units": c["units"], "workload": "light", "workload_note": "ok",
                         "courses": [{"code": c["code"], "satisfies": "x", "units": c["units"], "why": "w",
                                      "prereq_assumed": False}]}
                        for i, c in enumerate(payload["courses_to_schedule"])]
            return json.dumps({"summary": "s", "quarters": quarters, "cannot_do_at_foothill": [],
                               "needs_check": [], "warnings": []})
        planner._call = fake_call

    def tearDown(self):
        planner._call = self._call

    def test_claude_is_told_the_hours_rule_and_about_placeholders(self):
        reqs = gb.placeholder_reqs(gb.missing_slots({}, [])) + [major_req("MATH 20A", "MATH 1A")]
        res = planner.build_plan("CS", "UCSD", reqs, set(), 20, "Winter 2027", "Fall 2028", "works 15 hours a week")
        self.assertEqual(self.seen["payload"]["hours_per_unit_per_week"], 4)
        self.assertIn("4 hours of work each week for every unit", self.seen["system"])
        self.assertIn("placeholder", self.seen["system"])
        self.assertIn("works 15 hours a week", self.seen["payload"]["student_notes"])
        flagged = [c for c in self.seen["payload"]["courses_to_schedule"] if c.get("placeholder")]
        self.assertEqual(len(flagged), len([r for r in reqs if r.get("placeholder")]))
        self.assertEqual(res["issues_remaining"], [])
        first = res["plan"]["quarters"][0]
        self.assertIn("weekly_hours", first)
        titles = [c.get("title") for q in res["plan"]["quarters"] for c in q["courses"] if c.get("placeholder")]
        self.assertEqual(len(titles), len(flagged))

    def test_without_placeholders_nothing_changes(self):
        res = planner.build_plan("CS", "UCSD", [major_req("MATH 20A", "MATH 1A")], set(), 16, "W", "F", "")
        self.assertEqual([c["code"] for c in self.seen["payload"]["courses_to_schedule"]], ["MATH 1A"])
        self.assertEqual(res["plan"]["quarters"][0]["weekly_hours"], 20.0)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.old_db = store.db
        store.db = sqlite3.connect(self.tmp.name, check_same_thread=False)
        store.db.row_factory = sqlite3.Row
        store._init_db()

    def tearDown(self):
        store.db.close()
        store.db = self.old_db
        os.unlink(self.tmp.name)

    def test_round_trip_and_isolation(self):
        self.assertTrue(get_setting("a", "include_cal_getc", True))        # default
        set_setting("a", "include_cal_getc", False)
        self.assertFalse(get_setting("a", "include_cal_getc", True))
        self.assertTrue(get_setting("b", "include_cal_getc", True))        # another profile is unaffected
        set_setting("a", "include_cal_getc", True)
        self.assertTrue(get_setting("a", "include_cal_getc", False))
        self.assertIsNone(get_setting(None, "x"))
        set_setting(None, "x", 1)                                         # no profile: nothing happens


if __name__ == "__main__":
    unittest.main()