"""Say "semester" instead of "quarter" for a semester college, and name the student's own college instead of Foothill.
Foothill, a quarter college, gets every text unchanged."""
import re


def short_name(name):
    """'Evergreen Valley College' -> 'Evergreen Valley', so 'Evergreen Valley's list' reads naturally."""
    return re.sub(r"\s+(Community )?College$", "", str(name or "").strip()) or "Foothill"


def term_word(system):
    return "semester" if system == "semester" else "quarter"


def T(text, system="quarter", college=None, college_full=None):
    """Rewrite a screen text for the student's college. For a semester college 'quarters' becomes 'semesters' and the
    list of regular terms loses winter (only a short intersession there). Where the text says Foothill it names the
    student's college instead. A Foothill student gets the text unchanged."""
    if not text:
        return text
    if system == "semester":
        text = text.replace("Fall, Winter and Spring", "Fall and Spring")
        text = re.sub(r"[Qq]uarter", lambda m: "Semester" if m.group(0)[0] == "Q" else "semester", text)
    if college and college != "Foothill":
        if college_full:
            text = text.replace("Foothill College", college_full)
        text = text.replace("Foothill", college)
    return text
