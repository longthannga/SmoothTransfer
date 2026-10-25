import unittest
from pathlib import Path
from transcript_parser import parse_course_rows, extract_pdf_text

class TranscriptParserTests(unittest.TestCase):
    def test_extracts_generic_course_rows(self):
        rows = parse_course_rows("ENGL 1A  Composition and Reading     5.0  A\nMATH 1A  Calculus I     5.0  B")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["course_code"], "ENGL 1A")
        self.assertEqual(rows[0]["units"], 5.0)
        self.assertEqual(rows[0]["grade"], "A")

    def test_ignores_non_course_text(self):
        self.assertEqual(parse_course_rows("Official Transcript\nStudent Name: Example"), [])

    def test_foothill_wrapped_rows_and_terms(self):
        text = '''INSTITUTION CREDIT
Term: 2024 Summer Foothill
Subject Course Level Title Grade Credit
Hours
Quality
Points
C S F003A FU OBJECT ORIEN PRGM METH
PYTHON
A 4.500 18.00
Term Totals (Foothill Undergraduate)
TRANSFER CREDIT ACCEPTED BY INSTITUTION
202511: Advanced Placement Test
Subject Course Title Grade Credit Hours Quality Points R
MATH F001A CALCULUS P 4.000 0.00
COURSE(S) IN PROGRESS
Term: 2026 Summer Foothill
Subject Course Level Title Credit Hours
HUMN F015. FU ETHICS IN ARTIFICIAL INTELGNCE 4.000
'''
        rows = parse_course_rows(text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["course_code"], "CS F003A")
        self.assertEqual(rows[0]["title"], "OBJECT ORIEN PRGM METH PYTHON")
        self.assertEqual(rows[0]["grade"], "A")
        self.assertEqual(rows[0]["term"], "2024 Summer Foothill")
        self.assertEqual(rows[1]["course_code"], "MATH F001A")
        self.assertEqual(rows[1]["record_type"], "ap_ib_or_transfer")
        self.assertEqual(rows[2]["record_type"], "in_progress")
        self.assertEqual(rows[2]["grade"], "IP")

    def test_real_foothill_transcript(self):
        pdf = Path('/mnt/data/LongN_FH_07:14:26.pdf')
        if not pdf.exists():
            self.skipTest('uploaded transcript not mounted')
        result = extract_pdf_text(pdf.read_bytes(), use_claude_fallback=False)
        courses = result['courses']
        # Transcript contains 5 accepted transfer-credit rows, 25 institution rows, and 2 in-progress rows.
        self.assertEqual(len(courses), 32)
        self.assertEqual(sum(c['record_type'] == 'ap_ib_or_transfer' for c in courses), 5)
        self.assertEqual(sum(c['record_type'] == 'course' for c in courses), 25)
        self.assertEqual(sum(c['record_type'] == 'in_progress' for c in courses), 2)
        self.assertTrue(any(c['course_code'] == 'CS F003A' for c in courses))
        self.assertTrue(any(c['course_code'] == 'HUMN F015.' and c['record_type'] == 'in_progress' for c in courses))
        self.assertTrue(any(c['course_code'] == 'MATH F001A' and c['record_type'] == 'ap_ib_or_transfer' for c in courses))

if __name__ == '__main__':
    unittest.main()
