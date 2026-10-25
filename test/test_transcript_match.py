import unittest
from transcript_match import transcript_key, assist_key, classify_saved_courses

ASSIST = ["C S 1B", "C S 3A", "C S 2C", "C S 10", "MATH 1A", "MATH 1AH", "MATH 1B", "MATH 1C",
          "MATH 1D", "MATH 2B", "MATH 22", "CHEM 1A", "STAT C1000", "PHYS 4A"]


def rec(code, grade, kind="course", source="transcript_pdf", **kw):
    return {"course_code": code, "source": source, "institution": kw.get("institution", ""),
            "status": kw.get("status", "Needs review"),
            "notes": f"Extracted grade: {grade}; record_type: {kind}; parser: simple_parser"}


class MatchTests(unittest.TestCase):
    def test_keys(self):
        self.assertEqual(transcript_key("CS F003B"), ("CS3B", "foothill"))
        self.assertEqual(transcript_key("MATH F022."), ("MATH22", "foothill"))
        self.assertEqual(transcript_key("MATH F001AH"), ("MATH1AH", "foothill"))
        self.assertEqual(transcript_key("STAT C1000"), ("STATC1000", None))
        self.assertEqual(transcript_key("MATH D001A"), ("MATH1A", "deanza"))
        self.assertEqual(transcript_key("MATH 1A"), ("MATH1A", None))
        self.assertEqual(transcript_key("C S 1B"), ("CS1B", None))
        self.assertEqual(transcript_key("nonsense"), (None, None))
        self.assertEqual(assist_key("C S 3B"), "CS3B")

    def test_your_transcript_rows(self):
        saved = [rec("CS F003A", "A"), rec("CS F003B", "A"), rec("CS F002A", "A+"),
                 rec("MATH F001B", "A"), rec("MATH F001C", "A"), rec("MATH F022.", "A"),
                 rec("MATH F001A", "P", "ap_ib_or_transfer"),
                 rec("ANTH F001.", "A+"), rec("ENGL C1000H", "A")]
        out = classify_saved_courses(saved, ASSIST + ["C S 3B"])
        self.assertEqual(out["completed"], ["C S 3A", "C S 3B", "MATH 1A", "MATH 1B", "MATH 1C", "MATH 22"])
        self.assertIn("ANTH F001.", out["other"])  # not in this major's list
        self.assertEqual(out["not_passed"], [])

    def test_grades(self):
        saved = [rec("MATH F001A", "F"), rec("MATH F001A", "B"),        # failed, then retook and passed
                 rec("CHEM F001A", "F"),                                 # never passed
                 rec("MATH F001B", "D"),                                 # below C
                 rec("MATH F001C", "W"),
                 rec("CS F010.", "IP", "in_progress")]
        out = classify_saved_courses(saved, ASSIST)
        self.assertEqual(out["completed"], ["MATH 1A"])
        self.assertEqual(out["not_passed"], ["CHEM 1A", "MATH 1C"])
        self.assertEqual(out["below_c"], [("MATH F001B", "D")])
        self.assertEqual(out["in_progress"], ["C S 10"])

    def test_de_anza_is_not_foothill(self):
        out = classify_saved_courses([rec("MATH D001A", "A")], ASSIST)
        self.assertEqual(out["completed"], [])
        self.assertEqual(out["deanza"], ["MATH D001A"])

    def test_manual_entries(self):
        saved = [rec("MATH 1A", "", source="manual", institution="Foothill College"),
                 rec("MATH 1B", "", source="manual", institution="Some Other College"),
                 rec("MATH 1C", "", source="manual", status="Unmapped")]
        out = classify_saved_courses(saved, ASSIST)
        self.assertEqual(out["completed"], ["MATH 1A"])
        self.assertEqual(len(out["other"]), 2)


if __name__ == "__main__":
    unittest.main()