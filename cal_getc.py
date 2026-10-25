"""Cal-GETC area definitions for a planning checklist, not an official evaluator.

Course/score equivalency must be confirmed against the student's catalog year,
college articulation, and official Cal-GETC/AP/IB guidance.
"""
CAL_GETC_AREAS = [
    {"id": "1A", "name": "English Communication: English Composition", "courses": 1,
     "units": "At least 3 semester units", "note": "Course must be approved for Area 1A."},
    {"id": "1B", "name": "English Communication: Critical Thinking and Composition", "courses": 1,
     "units": "At least 3 semester units", "note": "Course must be approved for Area 1B."},
    {"id": "1C", "name": "English Communication: Oral Communication", "courses": 1,
     "units": "At least 3 semester units", "note": "Check the student's transfer pathway and current Cal-GETC rules."},
    {"id": "2", "name": "Mathematical Concepts and Quantitative Reasoning", "courses": 1,
     "units": "At least 3 semester units", "note": "Course must be approved for Area 2."},
    {"id": "3A", "name": "Arts", "courses": 1, "units": "At least 3 semester units", "note": "One approved course."},
    {"id": "3B", "name": "Humanities", "courses": 1, "units": "At least 3 semester units", "note": "One approved course."},
    {"id": "4", "name": "Social and Behavioral Sciences", "courses": 2,
     "units": "At least 6 semester units", "note": "Courses should be from different disciplines."},
    {"id": "5A", "name": "Physical Science", "courses": 1, "units": "At least 3 semester units", "note": "Approved physical science course."},
    {"id": "5B", "name": "Biological Science", "courses": 1, "units": "At least 3 semester units", "note": "Approved biological science course."},
    {"id": "5C", "name": "Laboratory Activity", "courses": 1, "units": "Lab component as specified by the approved course", "note": "May be linked to a science course; verify the college's approved list."},
    {"id": "6", "name": "Ethnic Studies", "courses": 1, "units": "At least 3 semester units", "note": "Course must be approved for Area 6."},
]

CAL_GETC_TOTAL_SEMESTER_UNITS = 34

CAL_GETC_NOTE = (
    "This checklist is a planning aid, not an official certification. Course approvals and "
    "unit rules depend on the community college, catalog year, and official Cal-GETC list. "
    "Confirm with a counselor before relying on it for transfer certification."
)
