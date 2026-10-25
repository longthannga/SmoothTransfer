"""Small persistence smoke tests; run with `python -m unittest test_store.py`."""
import os
import tempfile
import unittest

class StoreTests(unittest.TestCase):
    def setUp(self):
        # Import a fresh module against a temporary database so tests never touch user data.
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        import store
        self.store = store
        self.old_db = store.db
        import sqlite3
        store.db = sqlite3.connect(self.tmp.name, check_same_thread=False)
        store.db.row_factory = sqlite3.Row
        store._init_db()

    def tearDown(self):
        self.store.db.close()
        self.store.db = self.old_db
        os.unlink(self.tmp.name)

    def test_profile_round_trip(self):
        code = self.store.save_profile(None, major_label="Computer Science", completed=["MATH 1A"],
            max_units=16, start_term="Winter 2027", goal_term="Fall 2028", notes="", plan_res={"plan": {}})
        got = self.store.load_profile(code)
        self.assertEqual(got["completed"], ["MATH 1A"])
        self.assertEqual(got["plan"], {"plan": {}})

    def test_course_and_exam_records(self):
        code = self.store.save_profile(None, completed=[])
        self.store.add_course(code, course_code="ENGL 1A", status="Needs review")
        self.store.add_exam_credit(code, exam_type="AP", subject="Calculus AB", score="4")
        self.assertEqual(self.store.list_courses(code)[0]["course_code"], "ENGL 1A")
        self.assertEqual(self.store.list_exam_credits(code)[0]["score"], "4")

if __name__ == "__main__":
    unittest.main()
