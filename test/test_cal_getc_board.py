"""Cal-GETC board and sample transcript tests. Run with:  python -m unittest test_cal_getc_board.py"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BROWSER_WS", "wss://test")

import store
import cal_getc_board as board
from cal_getc import CAL_GETC_AREAS
from transcript_parser import extract_pdf_text

SAMPLE = Path(__file__).with_name("sample_transcript_test.pdf")


def rec(code, grade="A", kind="course", source="transcript_pdf", units=4.0, title="", term="2026 Winter Foothill"):
    return {"course_code": code, "title": title, "units": units, "term": term, "source": source,
            "notes": f"Extracted grade: {grade}; record_type: {kind}; parser: simple_parser" if source == "transcript_pdf" else ""}


class PlaceableTests(unittest.TestCase):
    def test_who_can_be_placed(self):
        out = board.placeable([
            rec("ENGL C1000", "A+"), rec("MATH F001A", "P", "ap_ib_or_transfer"),
            rec("HUMN F015.", "IP", "in_progress"), rec("MATH F009.", "D"), rec("ART F001.", "F"),
            rec("HIST F017A", "W"), rec("PHIL F001.", "", source="manual")])
        codes = {c["code"]: c for c in out["courses"]}
        self.assertEqual(sorted(codes), ["ENGL C1000", "HUMN F015.", "MATH F001A", "PHIL F001."])
        self.assertEqual(codes["HUMN F015."]["state"], "in_progress")
        self.assertIn("in progress", codes["HUMN F015."]["label"])
        self.assertIn("AP/transfer", codes["MATH F001A"]["label"])
        self.assertEqual(sorted(c for c, _ in out["blocked"]), ["ART F001.", "HIST F017A", "MATH F009."])
        reasons = dict(out["blocked"])
        self.assertIn("below C", reasons["MATH F009."])

    def test_best_attempt_wins_and_labels_are_unique(self):
        out = board.placeable([rec("MATH F001A", "F"), rec("MATH F001A", "B"), rec("MATH F001B", "A")])
        self.assertEqual(sorted(c["code"] for c in out["courses"]), ["MATH F001A", "MATH F001B"])
        self.assertEqual(out["blocked"], [])
        labels = [c["label"] for c in out["courses"]]
        self.assertEqual(len(labels), len(set(labels)))

    def test_empty_and_blank_codes(self):
        self.assertEqual(board.placeable([]), {"courses": [], "blocked": []})
        self.assertEqual(board.placeable([rec("  ", "A")])["courses"], [])


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.courses = board.placeable([rec("ENGL C1000", "A+", units=5), rec("ANTH F001.", "A+"),
                                        rec("ECON F001A", "B", units=5), rec("ETHN F001.", "A")])["courses"]

    def test_headers_are_unique_and_pool_comes_first(self):
        containers = board.build_containers(self.courses, {})
        headers = [c["header"] for c in containers]
        self.assertEqual(headers[0], board.POOL)
        self.assertEqual(len(headers), len(set(headers)))
        self.assertEqual(len(headers), 1 + len(CAL_GETC_AREAS))
        self.assertEqual(len(containers[0]["items"]), 4)

    def test_round_trip(self):
        placements = {"ENGL C1000": "1A", "ANTH F001.": "4", "ECON F001A": "4"}
        containers = board.build_containers(self.courses, placements)
        by_header = {c["header"]: c["items"] for c in containers}
        self.assertEqual(len(by_header[board.area_header(next(a for a in CAL_GETC_AREAS if a["id"] == "4"))]), 2)
        self.assertEqual(containers[0]["items"], [c["label"] for c in self.courses if c["key"] == "ETHN F001."])
        self.assertEqual(board.read_arrangement(containers, self.courses), placements)

    def test_moving_a_card_changes_the_result(self):
        containers = board.build_containers(self.courses, {})
        moved = [dict(c, items=list(c["items"])) for c in containers]
        label = next(c["label"] for c in self.courses if c["key"] == "ETHN F001.")
        moved[0]["items"].remove(label)
        six = next(g for g in moved if g["header"].startswith("6 ·"))
        six["items"].append(label)
        self.assertEqual(board.read_arrangement(moved, self.courses), {"ETHN F001.": "6"})

    def test_unknown_headers_and_labels_are_ignored(self):
        groups = [{"header": "Somewhere else", "items": ["ENGL C1000 · 5 units"]},
                  {"header": board.area_header(CAL_GETC_AREAS[0]), "items": ["not a course"]}]
        self.assertEqual(board.read_arrangement(groups, self.courses), {})
        self.assertEqual(board.read_arrangement(None, self.courses), {})

    def test_summary_counts(self):
        rows = {r["id"]: r for r in board.summarize(
            {"ENGL C1000": "1A", "ANTH F001.": "4", "ECON F001A": "4", "ETHN F001.": "6", "GONE": "2"}, self.courses)}
        self.assertTrue(rows["1A"]["enough"])
        self.assertEqual(rows["4"]["placed"], 2)
        self.assertEqual(rows["4"]["units"], 9.0)
        self.assertEqual(rows["2"]["placed"], 0)         # a course that no longer exists is not counted
        self.assertFalse(rows["2"]["enough"])
        need4 = next(a["courses"] for a in CAL_GETC_AREAS if a["id"] == "4")
        self.assertEqual(rows["4"]["enough"], 2 >= need4)


class StoreTests(unittest.TestCase):
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

    def test_save_replace_and_isolate(self):
        board.save_placements("a", {"ENGL C1000": "1A", "ANTH F001.": "4"})
        board.save_placements("b", {"ENGL C1000": "3B"})
        self.assertEqual(board.load_placements("a"), {"ENGL C1000": "1A", "ANTH F001.": "4"})
        self.assertEqual(board.load_placements("b"), {"ENGL C1000": "3B"})
        board.save_placements("a", {"ECON F001A": "4"})                       # replaces, does not add
        self.assertEqual(board.load_placements("a"), {"ECON F001A": "4"})
        board.save_placements("a", {"ECON F001A": "not-an-area"})             # bad area ids are ignored
        self.assertEqual(board.load_placements("a"), {})
        self.assertEqual(board.load_placements("nobody"), {})

    def test_placed_by_area_follows_the_students_courses(self):
        saved = [rec("ENGL C1000", "A+"), rec("ANTH F001.", "A+")]
        board.save_placements("a", {"ENGL C1000": "1A", "ANTH F001.": "4", "REMOVED 1": "4"})
        self.assertEqual(board.placed_by_area("a", saved), {"1A": ["ENGL C1000"], "4": ["ANTH F001."]})
        self.assertEqual(board.placed_by_area(None, saved), {})


@unittest.skipUnless(SAMPLE.exists(), "sample_transcript_test.pdf is not next to this file")
class SampleTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.result = extract_pdf_text(SAMPLE.read_bytes(), use_claude_fallback=False)
        self.rows = self.result["courses"]

    def test_the_parser_reads_every_row(self):
        kinds = [r["record_type"] for r in self.rows]
        self.assertEqual((len(self.rows), kinds.count("course"), kinds.count("ap_ib_or_transfer"),
                          kinds.count("in_progress")), (13, 9, 2, 2))
        self.assertEqual(self.result["parser_used"], "simple")

    def test_known_rows(self):
        by = {r["course_code"]: r for r in self.rows}
        self.assertEqual((by["CS F003B"]["grade"], by["CS F003B"]["units"]), ("A", 4.5))
        self.assertEqual(by["ENGL C1000"]["title"], "ACADEMIC READING AND WRITING")
        self.assertEqual(by["MATH F001B"]["grade"], "B+")
        self.assertEqual(by["MATH F001A"]["record_type"], "ap_ib_or_transfer")
        self.assertEqual(by["HUMN F015."]["grade"], "IP")

    def test_no_real_personal_details_in_the_file(self):
        text = self.result["text"]
        self.assertIn("Sample, Alex", text)
        self.assertNotIn("Nguyen", text)
        self.assertNotIn("Birth", text)

    def test_every_row_can_be_placed_on_the_board(self):
        saved = [dict(source="transcript_pdf", course_code=r["course_code"], title=r["title"], units=r["units"],
                      term=r["term"], notes=f"Extracted grade: {r['grade']}; record_type: {r['record_type']}; parser: simple")
                 for r in self.rows]
        out = board.placeable(saved)
        self.assertEqual(len(out["courses"]), 13)
        self.assertEqual(out["blocked"], [])


if __name__ == "__main__":
    unittest.main()
