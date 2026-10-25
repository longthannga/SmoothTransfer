"""AP credit tests. Run with:  python -m unittest test_ap.py

Uses a small PDF generated here (short excerpts of the UCSD AP chart wording) and a pretend Claude
answer, so no network and no API key are needed."""
import io
import json
import os
import sqlite3
import tempfile
import unittest

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("BROWSER_WS", "wss://test")

import ap
import store
from planner import needed_courses, not_schedulable, covered_by_ap
from requirements_parser import remaining, group_sections
from validator import check_plan

URL = "https://catalog.ucsd.edu/_files/advanced-placement-credit-chart-2026.pdf"

EXCERPT = """Calculus AB—4
Calculus BC—8
(8-unit maximum for both tests)
Score of 3 on AB exam = exempts MATH 10A.
Score of 4 or 5 on AB exam = exempts MATH 20A or 10A.
Score of 3 on BC exam = exempts MATH 20A or MATH 10A and 10B.
Score of 4 or 5 on BC exam = exempts MATH 20A, 20B or 10A, 10B.
Computer Science A (Java Programming)—8
Computer Science Principles—8
AP CS A: Score of 4 or 5 = exempts CSE 8A; student should take CSE 11.
AP CS Principles: Score of 4 or 5 = exempts CSE 3.
Chemistry—8
Score of 3 = exempts CHEM 4; may take CHEM 6A.
Score of 4 = exempts CHEM 6A; may take CHEM 6B or 6AH.
Score of 5 = exempts CHEM 6A, 6B, 6C; may take CHEM 6AH, 6BH, 6CH for credit.
Biology—8
Score of 3 = BILD 10; may take BILD 1, 2, 3 for credit.
Score of 4 or 5 = exempts BILD 1, 2, and 3.
Physics C Mechanics—4
C exam (Mech): Score of 3 = exempts PHYS 1A.
Score of 4 or 5 = exempts PHYS 1A or 2A.
A student cannot receive credit for a UC San Diego course that duplicates AP credit."""


def make_pdf(text=None):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in (text or EXCERPT).splitlines():
        c.drawString(40, y, line)
        y -= 14
    c.save()
    return buf.getvalue()


def rule(scores, courses, text):
    return {"scores": scores, "courses": courses, "text": text}


# What Claude is expected to send back, plus two deliberate mistakes it must not get away with.
RAW = {
    "chart_year": "2026-27",
    "duplicate_note": "A student cannot receive credit for a UC San Diego course that duplicates AP credit.",
    "exams": [
        {"exam": "Calculus AB", "units": 4, "unit_note": "", "major_exemptions": [
            rule([3], ["MATH 10A"], "Score of 3 on AB exam = exempts MATH 10A."),
            rule([4, 5], ["MATH 20A", "MATH 10A"], "Score of 4 or 5 on AB exam = exempts MATH 20A or 10A.")]},
        {"exam": "Calculus BC", "units": 8, "unit_note": "8-unit maximum for both tests", "major_exemptions": [
            rule([3], ["MATH 20A", "MATH 10A", "MATH 10B"], "Score of 3 on BC exam = exempts MATH 20A or MATH 10A and 10B."),
            rule([4, 5], ["MATH 20A", "MATH 20B", "MATH 10A", "MATH 10B"], "Score of 4 or 5 on BC exam = exempts MATH 20A, 20B or 10A, 10B."),
            rule([2], ["MATH 20B"], "Score of 2 on BC exam = exempts MATH 20B.")]},          # invented: not in the PDF
        {"exam": "Computer Science A", "units": 8, "unit_note": "", "major_exemptions": [
            rule([4, 5], ["CSE 8A"], "AP CS A: Score of 4 or 5 = exempts CSE 8A; student should take CSE 11.")]},
        {"exam": "Computer Science Principles", "units": 8, "unit_note": "", "major_exemptions": [
            rule([4, 5], ["CSE 3", "CSE 11"], "AP CS Principles: Score of 4 or 5 = exempts CSE 3.")]},  # CSE 11 not in the sentence
        {"exam": "Chemistry", "units": 8, "unit_note": "", "major_exemptions": [
            rule([3], ["CHEM 4"], "Score of 3 = exempts CHEM 4; may take CHEM 6A."),
            rule([4], ["CHEM 6A"], "Score of 4 = exempts CHEM 6A; may take CHEM 6B or 6AH."),
            rule([5], ["CHEM 6A", "CHEM 6B", "CHEM 6C"], "Score of 5 = exempts CHEM 6A, 6B, 6C; may take CHEM 6AH, 6BH, 6CH for credit.")]},
        {"exam": "Biology", "units": 8, "unit_note": "", "major_exemptions": [
            rule([3], ["BILD 10"], "Score of 3 = BILD 10; may take BILD 1, 2, 3 for credit."),
            rule([4, 5], ["BILD 1", "BILD 2", "BILD 3"], "Score of 4 or 5 = exempts BILD 1, 2, and 3.")]},
        {"exam": "Physics C: Mechanics", "units": 4, "unit_note": "", "major_exemptions": [
            rule([3], ["PHYS 1A"], "C exam (Mech): Score of 3 = exempts PHYS 1A."),
            rule([4, 5], ["PHYS 1A", "PHYS 2A"], "Score of 4 or 5 = exempts PHYS 1A or 2A.")]},
        {"exam": "Underwater Basket Weaving", "units": 4, "unit_note": "", "major_exemptions": [
            rule([5], ["ART 1"], "Score of 5 = exempts ART 1.")]},
        {"exam": "Precalculus", "units": None, "unit_note": "", "major_exemptions": []},
        # malformed pieces that must be dropped
        {"exam": "", "major_exemptions": []},
        {"exam": "Statistics", "units": "four", "major_exemptions": [
            {"scores": [9], "courses": ["MATH 11"], "text": "x"},
            {"scores": [3], "courses": ["not a course"], "text": "x"},
            {"scores": [3], "courses": ["MATH 11"], "text": ""}]},
    ],
}


def agreement_with(url):
    html = f'<p>AP and IB credit policies are below:</p><p><a href="{url}" rel="noopener" target="_blank">{url}</a></p>'
    return {"templateAssets": json.dumps([{"type": "GeneralText", "content": html}])}


def make_chart():
    chart = ap.clean_chart(RAW)
    return ap.verify_chart(chart, ap.pdf_text(make_pdf()))


def rq(course, options, section, status="articulated", pick=None):
    return {"course": course, "title": course, "units": 4, "section_id": section, "pick_n_of_section": pick,
            "status": status, "options": [[{"code": c, "units": 5} for c in g] for g in options], "note": None}


def exam(subject, score):
    return {"subject": subject, "score": str(score), "exam_type": "AP"}


class FindLinkTests(unittest.TestCase):
    def test_finds_links(self):
        html = ('<a href=\\"https://catalog.ucsd.edu/_files/advanced-placement-credit-chart-2026.pdf\\">x</a>'
                '<a href="https://catalog.ucsd.edu/_files/international-baccalaureate-credits-chart-2026.pdf">y</a>')
        res = {"templateAssets": json.dumps([{"type": "GeneralText", "content": html}, {"type": "RequirementGroup"}])}
        links = ap.find_policy_links(res)
        self.assertTrue(links["ap"].endswith("advanced-placement-credit-chart-2026.pdf"))
        self.assertTrue(links["ib"].endswith("international-baccalaureate-credits-chart-2026.pdf"))

    def test_no_links(self):
        self.assertEqual(ap.find_policy_links({"templateAssets": "[]"}), {"ap": None, "ib": None})
        self.assertEqual(ap.find_policy_links({}), {"ap": None, "ib": None})

    def test_safe_url(self):
        self.assertTrue(ap.safe_pdf_url(URL))
        for bad in ("http://catalog.ucsd.edu/x.pdf", "https://evil.com/x.pdf",
                    "https://catalog.ucsd.edu/page.html", "javascript:alert(1)", ""):
            self.assertFalse(ap.safe_pdf_url(bad), bad)


class CheckTests(unittest.TestCase):
    def test_text_comes_out_of_a_real_pdf(self):
        self.assertIn("exempts MATH 20A", ap.pdf_text(make_pdf()))

    def test_cleaning_drops_bad_pieces(self):
        chart = ap.clean_chart(RAW)
        names = [e["exam"] for e in chart["exams"]]
        self.assertNotIn("", names)
        stats = next(e for e in chart["exams"] if e["exam"] == "Statistics")
        self.assertEqual(stats["rules"], [])
        self.assertIsNone(stats["units"])
        self.assertGreaterEqual(chart["dropped"], 4)

    def test_verification_catches_invented_and_ungrounded_rules(self):
        chart = make_chart()
        by = {e["exam"]: e for e in chart["exams"]}
        self.assertTrue(chart["verification"]["checked"])
        self.assertTrue(chart["duplicate_note_verified"])
        self.assertTrue(all(r["verified"] for r in by["Calculus AB"]["rules"]))
        self.assertTrue(by["Calculus BC"]["rules"][0]["verified"])
        self.assertTrue(by["Calculus BC"]["rules"][1]["verified"])
        self.assertFalse(by["Calculus BC"]["rules"][2]["verified"])            # invented score-2 rule
        self.assertFalse(by["Computer Science Principles"]["rules"][0]["verified"])  # CSE 11 not in the sentence
        self.assertFalse(by["Underwater Basket Weaving"]["rules"][0]["verified"])
        self.assertFalse(by["Underwater Basket Weaving"]["name_found"])
        self.assertTrue(by["Physics C: Mechanics"]["name_found"])
        for e in ("Chemistry", "Biology", "Physics C: Mechanics", "Computer Science A"):
            self.assertTrue(all(r["verified"] for r in by[e]["rules"]), e)

    def test_no_pdf_text_means_nothing_is_verified(self):
        chart = ap.verify_chart(ap.clean_chart(RAW), "")
        self.assertFalse(chart["verification"]["checked"])
        self.assertFalse(any(r["verified"] for e in chart["exams"] for r in e["rules"]))


class EffectTests(unittest.TestCase):
    def setUp(self):
        self.chart = make_chart()
        self.reqs = [rq("MATH 20A", [["MATH 1A"]], "a"), rq("MATH 20B", [["MATH 1B"]], "a"),
                     rq("MATH 20C", [["MATH 1C", "MATH 1D"]], "a"), rq("CSE 11", [["C S 1B"]], "a"),
                     rq("CHEM 6A", [["CHEM 1A"]], "b"), rq("CHEM 6B", [["CHEM 1B"]], "b"),
                     rq("BILD 1", [["BIOL 1A"]], "c"), rq("BILD 10", [["BIOL 10"]], "c")]

    def covered(self, exams, **kw):
        return sorted(r["course"] for r in ap.apply_ap(self.reqs, self.chart, exams, **kw) if r.get("ap_met"))

    def test_calculus(self):
        self.assertEqual(self.covered([exam("Calculus BC", 5)]), ["MATH 20A", "MATH 20B"])
        self.assertEqual(self.covered([exam("Calculus BC", 3)]), ["MATH 20A"])
        self.assertEqual(self.covered([exam("Calculus AB", 3)]), [])   # AB 3 only exempts MATH 10A
        self.assertEqual(self.covered([exam("Calculus AB", 4)]), ["MATH 20A"])

    def test_best_score_wins_and_low_scores_give_nothing(self):
        self.assertEqual(self.covered([exam("Calculus BC", 3), exam("Calculus BC", 5)]), ["MATH 20A", "MATH 20B"])
        self.assertEqual(self.covered([exam("Calculus BC", 2)]), [])      # the invented score-2 rule is not applied
        self.assertEqual(ap.exam_effects(self.chart, "Calculus BC", 2)["status"], "no_credit")

    def test_cs_a_exempts_8a_but_not_cse_11(self):
        self.assertEqual(self.covered([exam("Computer Science A", 5)]), [])
        fx = ap.exam_effects(self.chart, "Computer Science A", 5)
        self.assertEqual(fx["courses"], ["CSE 8A"])

    def test_chemistry_and_biology(self):
        self.assertEqual(self.covered([exam("Chemistry", 4)]), ["CHEM 6A"])
        self.assertEqual(self.covered([exam("Chemistry", 5)]), ["CHEM 6A", "CHEM 6B"])  # 5 also exempts 6B and 6C
        self.assertEqual(self.covered([exam("Chemistry", 3)]), [])   # 3 exempts only CHEM 4; CHEM 6A is "may take"
        self.assertEqual(self.covered([exam("Biology", 3)]), ["BILD 10"])
        self.assertEqual(self.covered([exam("Biology", 4)]), ["BILD 1"])

    def test_unverified_rules_need_the_students_say_so(self):
        e = [exam("Computer Science Principles", 5)]
        self.assertEqual(ap.exam_effects(self.chart, e[0]["subject"], 5)["status"], "unverified")
        self.assertEqual(self.covered(e), [])
        self.assertEqual(self.covered(e, allow_unverified=True), ["CSE 11"])

    def test_names_and_scores_that_do_not_match(self):
        self.assertEqual(self.covered([exam("Calculus  bc", 5)]), ["MATH 20A", "MATH 20B"])  # spacing and case
        self.assertEqual(self.covered([exam("Unknown Exam", 5), exam("Calculus BC", "five")]), [])
        self.assertEqual(ap.exam_effects(self.chart, "Unknown Exam", 5)["status"], "not_in_chart")

    def test_no_chart_changes_nothing(self):
        self.assertEqual([r.get("ap_met") for r in ap.apply_ap(self.reqs, None, [exam("Calculus BC", 5)])],
                         [None] * len(self.reqs))

    def test_removing_the_exam_removes_the_flag(self):
        on = ap.apply_ap(self.reqs, self.chart, [exam("Calculus BC", 5)])
        self.assertTrue(on[0]["ap_met"])
        off = ap.apply_ap(on, self.chart, [])
        self.assertNotIn("ap_met", off[0])
        self.assertNotIn("ap_met", self.reqs[0])      # the original list is never changed


class PlanningTests(unittest.TestCase):
    """The planner, checker and counters all honour ap_met."""

    def setUp(self):
        chart = make_chart()
        self.reqs = [rq("MATH 20A", [["MATH 1A"]], "a"), rq("MATH 20B", [["MATH 1B"]], "a"),
                     rq("CHEM 6A", [["CHEM 1A"]], "b", pick=1), rq("BILD 1", [["BIOL 1A"]], "b", pick=1),
                     rq("CSE 21", [], "c", status="unknown")]
        self.bc5 = ap.apply_ap(self.reqs, chart, [exam("Calculus BC", 5)])
        self.chem4 = ap.apply_ap(self.reqs, chart, [exam("Chemistry", 4)])

    def codes(self, reqs, done=()):
        return sorted(c["code"] for c in needed_courses(reqs, set(done)))

    def test_without_ap(self):
        got = self.codes(self.reqs)
        self.assertEqual(len(got), 3)                              # two math courses and one science course
        self.assertIn("MATH 1A", got)
        self.assertIn("MATH 1B", got)

    def test_ap_removes_courses(self):
        self.assertEqual(len(self.codes(self.bc5)), 1)            # only one science course left
        self.assertNotIn("MATH 1A", self.codes(self.bc5))

    def test_ap_can_satisfy_a_pick_one_section(self):
        self.assertEqual(self.codes(self.chem4), ["MATH 1A", "MATH 1B"])

    def test_checker_accepts_an_empty_plan_when_ap_covers_everything(self):
        both = ap.apply_ap(self.reqs, make_chart(), [exam("Calculus BC", 5), exam("Chemistry", 4)])
        self.assertEqual(check_plan({"quarters": []}, both, set(), 16), [])
        self.assertTrue(check_plan({"quarters": []}, self.reqs, set(), 16))     # without AP it complains

    def test_remaining_and_counts(self):
        done, todo = remaining(self.bc5, set())
        self.assertEqual(sorted(r["course"] for r in done), ["MATH 20A", "MATH 20B"])
        g = {x["id"]: x for x in group_sections(self.bc5)}
        self.assertEqual((g["a"]["ap_covered"], g["a"]["required"]), (2, 0))
        self.assertEqual((g["b"]["ap_covered"], g["b"]["required"]), (0, 1))

    def test_unarticulated_requirement_covered_by_ap_is_not_flagged(self):
        reqs = [rq("MATH 20A", [], "a", status="none")]
        reqs[0]["note"] = "No Course Articulated"
        flagged = ap.apply_ap(reqs, make_chart(), [exam("Calculus BC", 5)])
        self.assertEqual(not_schedulable(reqs)[0]["course"], "MATH 20A")
        self.assertEqual(not_schedulable(flagged), [])

    def test_summary_for_claude(self):
        out = covered_by_ap(self.bc5)
        self.assertEqual(sorted(x["requirement"] for x in out), ["MATH 20A", "MATH 20B"])
        self.assertEqual(out[0]["by"], ["Calculus BC (score 5)"])


class OverlapTests(unittest.TestCase):
    def test_overlap_warning(self):
        chart = make_chart()
        reqs = ap.apply_ap([rq("MATH 20A", [["MATH 1A"]], "a")], chart, [exam("Calculus BC", 5)])
        warn = ap.ap_overlaps(reqs, {"MATH 1A"}, [], chart=chart)
        self.assertEqual(len(warn), 1)
        self.assertIn("MATH 1A", warn[0])
        self.assertIn("cannot also earn credit", warn[0])
        planned = [{"courses": [{"code": "MATH 1A"}]}]
        self.assertEqual(len(ap.ap_overlaps(reqs, set(), planned, chart=chart)), 1)

    def test_foothill_credit_that_came_from_the_ap_exam_is_not_a_duplicate(self):
        reqs = ap.apply_ap([rq("MATH 20A", [["MATH 1A"]], "a")], make_chart(), [exam("Calculus BC", 5)])
        self.assertEqual(ap.ap_overlaps(reqs, {"MATH 1A"}, [], ap_derived={"MATH 1A"}), [])

    def test_no_warning_without_overlap(self):
        reqs = ap.apply_ap([rq("MATH 20A", [["MATH 1A"]], "a")], make_chart(), [exam("Calculus BC", 5)])
        self.assertEqual(ap.ap_overlaps(reqs, set(), []), [])


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.old_db = store.db
        store.db = sqlite3.connect(self.tmp.name, check_same_thread=False)
        store.db.row_factory = sqlite3.Row
        store._init_db()
        self.pdf = make_pdf()
        self.calls = {"fetch": 0, "extract": 0}
        self.fail_fetch = False
        self._fetch, self._extract = ap.fetch_pdf, ap.extract_chart

        def fake_fetch(url):
            self.calls["fetch"] += 1
            if self.fail_fetch:
                raise ap.FetchError("blocked (403)")
            return self.pdf, "test download"

        def fake_extract(pdf, kind="ap"):
            self.calls["extract"] += 1
            return json.loads(json.dumps(RAW))

        ap.fetch_pdf, ap.extract_chart = fake_fetch, fake_extract

    def tearDown(self):
        ap.fetch_pdf, ap.extract_chart = self._fetch, self._extract
        store.db.close()
        store.db = self.old_db
        os.unlink(self.tmp.name)

    def test_load_cache_and_reuse(self):
        res = ap.load_chart(agreement_with(URL))
        self.assertEqual(res["status"], "ok")
        self.assertEqual(self.calls, {"fetch": 1, "extract": 1})
        again = ap.load_chart(agreement_with(URL))
        self.assertEqual(again["status"], "cached")
        self.assertEqual(self.calls, {"fetch": 1, "extract": 1})          # nothing downloaded
        live = ap.load_chart(agreement_with(URL), force_live=True)
        self.assertEqual(live["status"], "ok")
        self.assertEqual(self.calls, {"fetch": 2, "extract": 1})          # downloaded again, Claude not asked again
        self.assertTrue(live["chart"]["verification"]["checked"])

    def test_stale_copy_when_the_download_fails(self):
        ap.load_chart(agreement_with(URL))
        self.fail_fetch = True
        res = ap.load_chart(agreement_with(URL), force_live=True)
        self.assertEqual(res["status"], "stale")
        self.assertIn("blocked (403)", res["message"])
        self.assertTrue(res["chart"]["exams"])

    def test_failure_with_nothing_saved(self):
        self.fail_fetch = True
        res = ap.load_chart(agreement_with(URL))
        self.assertEqual(res["status"], "fetch_failed")

    def test_no_link_and_unsafe_link(self):
        self.assertEqual(ap.load_chart({"templateAssets": "[]"})["status"], "no_link")
        bad = agreement_with("https://evil.example.com/advanced-placement-credit-chart.pdf")
        self.assertEqual(ap.load_chart(bad)["status"], "unsafe_link")
        self.assertEqual(self.calls["fetch"], 0)

    def test_claude_failure_is_reported(self):
        def boom(pdf, kind="ap"):
            raise ValueError("model unavailable")
        ap.extract_chart = boom
        res = ap.load_chart(agreement_with(URL))
        self.assertEqual(res["status"], "extract_failed")
        self.assertIn("model unavailable", res["message"])
        ap.extract_chart = lambda pdf, kind="ap": {"exams": []}
        self.assertEqual(ap.load_chart(agreement_with(URL))["status"], "extract_failed")

    def test_delete_exam(self):
        code = store.save_profile(None, completed=[])
        i = store.add_exam_credit(code, exam_type="AP", subject="Calculus BC", score="5")
        ap.delete_exam("someone-else", i)
        self.assertEqual(len(store.list_exam_credits(code)), 1)    # another profile cannot delete it
        ap.delete_exam(code, i)
        self.assertEqual(store.list_exam_credits(code), [])


# ---------------- IB ----------------

IB_EXCERPT = """Biology—8
Score of 5 = exempts BILD 10.
Score of 6 or 7 = exempts BILD 1, 2, 3.
Chemistry—8
Score of 5 = exempts CHEM 6A.
Score of 6 = exempts CHEM 6A and 6C.
Score of 7 = exempts 6A, 6B, 6C.
Computer Science—8
Score of 6 or 7 = CSE 3.
Mathematics: Applications and Interpretations
Not eligible for credit
Mathematics: Analysis and Approaches—8
Score of 5, 6, or 7 = Exempts Math 20A and MATH 10B
Physics—8
Score of 5 = exempts Phys 10 or 11.
Business and Management—8
Score of 5, 6, or 7 = elective credit only.
If the student does duplicate an exam with another exam of the same subject content, and/or an exam with a college course, credit will be awarded only once."""

IB_URL = "https://catalog.ucsd.edu/_files/international-baccalaureate-credits-chart-2026.pdf"

RAW_IB = {
    "chart_year": "2026-27",
    "duplicate_note": "If the student does duplicate an exam with another exam of the same subject content, and/or an exam with a college course, credit will be awarded only once.",
    "exams": [
        {"exam": "Biology", "units": 8, "unit_note": "", "major_exemptions": [
            rule([5], ["BILD 10"], "Score of 5 = exempts BILD 10."),
            rule([6, 7], ["BILD 1", "BILD 2", "BILD 3"], "Score of 6 or 7 = exempts BILD 1, 2, 3."),
            rule([4], ["BILD 1"], "Score of 4 = exempts BILD 1.")]},                       # invented
        {"exam": "Chemistry", "units": 8, "unit_note": "", "major_exemptions": [
            rule([5], ["CHEM 6A"], "Score of 5 = exempts CHEM 6A."),
            rule([6], ["CHEM 6A", "CHEM 6C"], "Score of 6 = exempts CHEM 6A and 6C."),
            rule([7], ["CHEM 6A", "CHEM 6B", "CHEM 6C"], "Score of 7 = exempts 6A, 6B, 6C.")]},
        {"exam": "Computer Science", "units": 8, "unit_note": "", "major_exemptions": [
            rule([6, 7], ["CSE 3"], "Score of 6 or 7 = CSE 3.")]},
        {"exam": "Mathematics: Applications and Interpretations", "units": None, "unit_note": "", "major_exemptions": []},
        {"exam": "Mathematics: Analysis and Approaches", "units": 8, "unit_note": "", "major_exemptions": [
            rule([5, 6, 7], ["math 20a", "MATH 10B"], "Score of 5, 6, or 7 = Exempts Math 20A and MATH 10B")]},
        {"exam": "Physics", "units": 8, "unit_note": "", "major_exemptions": [
            rule([5], ["PHYS 10", "PHYS 11"], "Score of 5 = exempts Phys 10 or 11.")]},
        {"exam": "Business and Management", "units": 8, "unit_note": "", "major_exemptions": []},
    ],
}


def make_ib_chart():
    chart = ap.clean_chart(RAW_IB, kind="ib")
    return ap.verify_chart(chart, ap.pdf_text(make_pdf(IB_EXCERPT)))


def ib_exam(subject, score):
    return {"subject": subject, "score": str(score), "exam_type": "IB"}


class IBTests(unittest.TestCase):
    def setUp(self):
        self.chart = make_ib_chart()
        self.reqs = [rq("BILD 1", [["BIOL 1A"]], "a"), rq("BILD 10", [["BIOL 10"]], "a"), rq("MATH 20A", [["MATH 1A"]], "b"),
                     rq("MATH 20B", [["MATH 1B"]], "b"), rq("CHEM 6B", [["CHEM 1B"]], "c"), rq("CSE 3", [["C S 3"]], "d")]

    def covered(self, exams, charts=None, **kw):
        out = ap.apply_ap(self.reqs, charts or {"IB": self.chart}, exams, **kw)
        return sorted(r["course"] for r in out if r.get("ap_met"))

    def test_the_ib_scale_goes_to_seven(self):
        scores = {s for e in self.chart["exams"] for r in e["rules"] for s in r["scores"]}
        self.assertIn(7, scores)
        self.assertEqual(self.chart["kind"], "ib")
        ap_chart = ap.clean_chart(RAW_IB, kind="ap")           # the same data read as AP drops scores above 5
        self.assertNotIn(7, {s for e in ap_chart["exams"] for r in e["rules"] for s in r["scores"]})

    def test_codes_are_tidied_and_checked_against_the_pdf(self):
        math = next(e for e in self.chart["exams"] if e["exam"] == "Mathematics: Analysis and Approaches")
        self.assertEqual(math["rules"][0]["courses"], ["MATH 20A", "MATH 10B"])          # "math 20a" became "MATH 20A"
        self.assertTrue(math["rules"][0]["verified"])
        bio = next(e for e in self.chart["exams"] if e["exam"] == "Biology")
        self.assertEqual([r["verified"] for r in bio["rules"]], [True, True, False])      # the invented score-4 rule
        self.assertTrue(self.chart["duplicate_note_verified"])

    def test_scores_decide_what_is_exempted(self):
        self.assertEqual(self.covered([ib_exam("Biology", 5)]), ["BILD 10"])
        self.assertEqual(self.covered([ib_exam("Biology", 6)]), ["BILD 1"])
        self.assertEqual(self.covered([ib_exam("Chemistry", 6)]), [])                    # 6 exempts 6A and 6C, not 6B
        self.assertEqual(self.covered([ib_exam("Chemistry", 7)]), ["CHEM 6B"])
        self.assertEqual(self.covered([ib_exam("Mathematics: Analysis and Approaches", 5)]), ["MATH 20A"])
        self.assertEqual(self.covered([ib_exam("Computer Science", 6)]), ["CSE 3"])
        self.assertEqual(self.covered([ib_exam("Computer Science", 5)]), [])

    def test_below_five_earns_no_credit_and_the_invented_rule_is_not_applied(self):
        self.assertEqual(ap.exam_effects(self.chart, "Biology", 4)["status"], "no_credit")
        self.assertEqual(self.covered([ib_exam("Biology", 4)]), [])
        self.assertEqual(self.covered([ib_exam("Biology", 4)], allow_unverified=True), ["BILD 1"])

    def test_exams_with_nothing_for_the_major(self):
        self.assertEqual(ap.exam_effects(self.chart, "Business and Management", 6)["status"], "no_major_exemption")
        self.assertEqual(ap.exam_effects(self.chart, "Mathematics: Applications and Interpretations", 7)["status"], "no_major_exemption")

    def test_ap_and_ib_exams_with_the_same_name_stay_separate(self):
        ap_chart = make_chart()
        exams = [exam("Biology", 4), ib_exam("Biology", 5)]
        out = ap.apply_ap(self.reqs, {"AP": ap_chart, "IB": self.chart}, exams)
        by = {r["course"]: r for r in out if r.get("ap_met")}
        self.assertEqual(by["BILD 1"]["ap_by"], ["Biology (score 4)"])                  # AP Biology 4 exempts BILD 1, 2, 3
        self.assertEqual(by["BILD 10"]["ap_by"], ["IB Biology (score 5)"])              # IB Biology 5 exempts BILD 10

    def test_an_exam_with_no_chart_loaded_changes_nothing(self):
        self.assertEqual(self.covered([ib_exam("Biology", 6)], charts={"AP": make_chart()}), [])
        self.assertEqual(ap.exempt_map(None, [ib_exam("Biology", 6)]), {})

    def test_one_chart_passed_alone_still_means_ap(self):
        self.assertEqual(sorted(r["course"] for r in ap.apply_ap(self.reqs, make_chart(), [exam("Biology", 4)]) if r.get("ap_met")),
                         ["BILD 1"])


class IBLoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.old_db = store.db
        store.db = sqlite3.connect(self.tmp.name, check_same_thread=False)
        store.db.row_factory = sqlite3.Row
        store._init_db()
        self.pdfs = {URL: make_pdf(), IB_URL: make_pdf(IB_EXCERPT)}
        self.asked = []
        self._fetch, self._extract = ap.fetch_pdf, ap.extract_chart
        ap.fetch_pdf = lambda url: (self.pdfs[url], "test download")

        def fake_extract(pdf, kind="ap"):
            self.asked.append(kind)
            return json.loads(json.dumps(RAW_IB if kind == "ib" else RAW))
        ap.extract_chart = fake_extract
        both = '<a href="%s">AP</a><a href="%s">IB</a>' % (URL, IB_URL)
        self.agreement = {"templateAssets": json.dumps([{"type": "GeneralText", "content": both}])}

    def tearDown(self):
        ap.fetch_pdf, ap.extract_chart = self._fetch, self._extract
        store.db.close()
        store.db = self.old_db
        os.unlink(self.tmp.name)

    def test_each_kind_loads_its_own_chart(self):
        ib = ap.load_chart(self.agreement, kind="ib")
        self.assertEqual((ib["status"], ib["url"], ib["chart"]["kind"]), ("ok", IB_URL, "ib"))
        a = ap.load_chart(self.agreement, kind="ap")
        self.assertEqual((a["status"], a["url"], a["chart"]["kind"]), ("ok", URL, "ap"))
        self.assertEqual(self.asked, ["ib", "ap"])                                      # Claude is told which chart it is reading
        self.assertTrue(ib["chart"]["verification"]["checked"])
        self.assertEqual(ap.load_chart(self.agreement, kind="ib")["status"], "cached")  # and each is cached on its own

    def test_no_ib_link(self):
        only_ap = {"templateAssets": json.dumps([{"type": "GeneralText", "content": '<a href="%s">AP</a>' % URL}])}
        res = ap.load_chart(only_ap, kind="ib")
        self.assertEqual(res["status"], "no_link")
        self.assertIn("IB", res["message"])
        self.assertEqual(ap.load_chart(only_ap, kind="ap")["status"], "ok")

    def test_the_prompts_differ(self):
        self.assertIn("International Baccalaureate", ap.SYSTEMS["ib"])
        self.assertIn("1 to 7", ap.SYSTEMS["ib"])
        self.assertIn("Advanced Placement", ap.SYSTEMS["ap"])
        self.assertIn("1 to 5", ap.SYSTEMS["ap"])


if __name__ == "__main__":
    unittest.main()