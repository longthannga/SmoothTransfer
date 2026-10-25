"""Transcript PDF extraction with a deterministic parser and optional Claude fallback.

The parser extracts what the transcript says; it does not determine transferability
or requirement satisfaction. Every extracted row should be reviewed before saving.
"""
from __future__ import annotations

import io
import json
import os
import re
from typing import Any

# Generic single-line layouts, useful for transcripts other than Foothill's.
COURSE_ROW = re.compile(
    r"^\s*(?P<code>[A-Z]{2,6}[\s-]*[A-Z]?[0-9]{1,4}[A-Z]?(?:\s*[-.]\s*[0-9]+)?)"
    r"\s+(?P<rest>.+?)\s{2,}(?P<units>\d+(?:\.\d+)?)"
    r"(?:\s{2,}(?P<grade>A\+?|A-|B\+?|B-|C\+?|C-|D\+?|D-|F|P|NP|IP|W|I|CR|NC)\b.*)?$", re.I)

# Foothill-De Anza transcript: subject and course are separate columns. PDF text
# extraction sometimes splits the subject "CS" into "C S" and wraps titles/grades.
FH_ROW_START = re.compile(
    r"^\s*(?P<subject>(?:[A-Z]\s+[A-Z]?|[A-Z]{2,5}))\s+"
    r"(?P<code>[A-Z]\d{3,4}[A-Z.]?)\s+(?:(?P<level>FU)\s+)?(?P<body>.*)$", re.I)
FH_GRADE_END = re.compile(
    r"(?:^|\s)(?P<grade>A\+|A-|A|B\+|B-|B|C\+|C-|C|D\+|D-|D|F|P|NP|IP|W|I|CR|NC)\s+"
    r"(?P<units>\d+(?:\.\d{3})?)\s+(?P<points>\d+(?:\.\d{2})?)\s*$", re.I)
FH_AP_END = re.compile(
    r"(?:^|\s)(?P<grade>A\+|A-|A|B\+|B-|B|C\+|C-|C|D\+|D-|D|F|P|NP|IP|W|I|CR|NC)\s+"
    r"(?P<units>\d+(?:\.\d{3})?)\s+(?P<points>\d+(?:\.\d{2})?)\s*$", re.I)
FH_IN_PROGRESS_END = re.compile(r"(?P<units>\d+(?:\.\d{3})?)\s*$")

TERM_RE = re.compile(r"^Term:\s*(.+)$", re.I)
STOP_RE = re.compile(
    r"^(?:Term Totals|Cumulative\b|Current Term\b|Transcript Totals|Total Institution|"
    r"Attempt Hours|Passed Hours|Earned Hours|GPA Hours|Quality Points|GPA\b|"
    r"Academic Standing|Additional Standing|Good Standing|Dean's List|"
    r"TRANSFER CREDIT ACCEPTED BY INSTITUTION|INSTITUTION CREDIT|COURSE\(S\) IN PROGRESS|"
    r"DEGREE AWARDED|STUDENT INFORMATION|Transcript Data|Subject\s+Course)\b", re.I)


def _clean_subject(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def _normalise_code(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().upper()


def _row(code: str, title: str, units: float | None, grade: str = "", *,
         term: str = "", record_type: str = "course", source: str = "simple_parser") -> dict[str, Any]:
    return {
        "course_code": _normalise_code(code),
        "title": re.sub(r"\s+", " ", title).strip(" \t-"),
        "units": units,
        "grade": grade.upper().strip(),
        "term": term,
        "record_type": record_type,
        "status": "Needs review",
        "parser_source": source,
    }


def _parse_foothill(text: str) -> list[dict[str, Any]]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    rows: list[dict[str, Any]] = []
    section = ""
    term = ""
    pending: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        raw = re.sub(r"\s+", " ", pending.get("raw", "")).strip()
        kind = pending["record_type"]
        if kind == "in_progress":
            end = FH_IN_PROGRESS_END.search(raw)
            if end:
                units = float(end.group("units"))
                title = raw[:end.start()].strip()
                rows.append(_row(pending["code"], title, units, "IP", term=term,
                                 record_type=kind))
        else:
            end = FH_GRADE_END.search(raw)
            if end:
                units = float(end.group("units"))
                grade = end.group("grade").upper()
                title = raw[:end.start()].strip()
                rows.append(_row(pending["code"], title, units, grade, term=term,
                                 record_type=kind))
        pending = None

    for line in lines:
        if not line:
            continue
        term_match = TERM_RE.match(line)
        if term_match:
            flush()
            term = term_match.group(1).strip()
            continue
        if line.upper().startswith("TRANSFER CREDIT ACCEPTED BY INSTITUTION"):
            flush(); section = "transfer"; term = ""
            continue
        if line.upper().startswith("INSTITUTION CREDIT"):
            flush(); section = "institution"
            continue
        if line.upper().startswith("COURSE(S) IN PROGRESS"):
            flush(); section = "in_progress"
            continue
        if STOP_RE.match(line):
            flush()
            continue

        start = FH_ROW_START.match(line)
        if start:
            flush()
            subject = _clean_subject(start.group("subject"))
            course = _normalise_code(start.group("code"))
            level = (start.group("level") or "").upper()
            kind = "ap_ib_or_transfer" if section == "transfer" else (
                "in_progress" if section == "in_progress" else "course")
            body = start.group("body").strip()
            # Transfer-credit rows omit level; institution rows include FU. Only
            # accept these rows inside a known transcript course section.
            if section in {"transfer", "institution", "in_progress"}:
                pending = {"code": f"{subject} {course}", "raw": body,
                           "record_type": kind, "level": level}
            continue

        # Wrapped title/grade/units rows belong to the current course. Ignore
        # other transcript text rather than accidentally treating it as a title.
        if pending:
            if STOP_RE.match(line):
                flush()
            else:
                pending["raw"] = (pending.get("raw", "") + " " + line).strip()
                # Completed rows have grade + units + quality points at the end.
                if pending["record_type"] != "in_progress" and FH_GRADE_END.search(pending["raw"]):
                    flush()
                elif pending["record_type"] == "in_progress" and FH_IN_PROGRESS_END.search(line):
                    flush()
    flush()
    return _dedupe(rows)


def _parse_generic(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = COURSE_ROW.match(line)
        if not match:
            continue
        code = _normalise_code(match.group("code"))
        rest = match.group("rest").strip()
        units = float(match.group("units"))
        grade = (match.group("grade") or "").upper()
        rows.append(_row(code, rest, units, grade))
    return _dedupe(rows)


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen = set()
    for row in rows:
        key = (row["course_code"], row["title"].casefold(), row["units"], row["grade"],
               row.get("term", ""), row.get("record_type", "course"))
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def parse_course_rows(text: str) -> list[dict[str, Any]]:
    """Parse Foothill-style transcript rows first, then try a generic layout."""
    foothill = _parse_foothill(text)
    generic = _parse_generic(text)
    # Prefer the Foothill parser when it recognizes a Foothill transcript, because
    # it preserves separate subject/course columns and course terms.
    if foothill:
        return foothill
    return generic


def parse_with_claude(text: str) -> list[dict[str, Any]]:
    """Use Claude as a fallback for layouts the deterministic parser misses.

    Requires ANTHROPIC_API_KEY. Claude output is still unverified and should be
    shown to the user for review; it must never directly establish equivalency.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not text.strip():
        return []
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        response = client.messages.create(
            model=os.getenv("TRANSCRIPT_PARSER_MODEL", "claude-haiku-5-5"),
            max_tokens=5000,
            temperature=0,
            system=(
                "Extract course records from transcript text. Return ONLY valid JSON: "
                "{\"courses\":[{\"course_code\":string,\"title\":string,\"units\":number|null,"
                "\"grade\":string,\"term\":string,\"record_type\":\"course\"|\"ap_ib_or_transfer\"|\"in_progress\"}]} . "
                "Do not infer transfer equivalencies or requirement satisfaction. Do not include totals, "
                "GPA summaries, headings, or student personal information. Preserve codes, titles, units, "
                "grades and terms as written. If uncertain, omit the row."
            ),
            messages=[{"role": "user", "content": "Extract course records from this transcript text:\n\n" + text[:45000]}],
        )
        content = "\n".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        # Be tolerant if a model wraps JSON in a code fence.
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            return []
        data = json.loads(match.group(0))
        output = []
        for item in data.get("courses", []):
            code = str(item.get("course_code", "")).strip()
            title = str(item.get("title", "")).strip()
            if not code or not title:
                continue
            units = item.get("units")
            try:
                units = float(units) if units is not None else None
            except (TypeError, ValueError):
                units = None
            kind = str(item.get("record_type", "course"))
            if kind not in {"course", "ap_ib_or_transfer", "in_progress"}:
                kind = "course"
            output.append(_row(code, title, units, str(item.get("grade", "")),
                               term=str(item.get("term", "")), record_type=kind,
                               source="claude_fallback"))
        return _dedupe(output)
    except Exception:
        # Fallback should not prevent manual upload/review if API/model is unavailable.
        return []


def extract_pdf_text(file_bytes: bytes, *, use_claude_fallback: bool = True) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF extraction requires pypdf. Install it with: pip install pypdf") from exc
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    text = "\n".join(pages)
    if not text.strip():
        return {"text": "", "courses": [], "needs_ocr": True, "parser_used": "none",
                "message": "No selectable text was found. This may be a scanned PDF; OCR or manual entry is needed."}

    courses = parse_course_rows(text)
    parser_used = "simple"
    # Detect likely partial parses: on Foothill transcripts, each course row begins
    # with a subject + course code. If the parser recognized fewer rows than the
    # visible row starts, ask Claude to try the layout rather than silently accepting
    # an incomplete result. Normal fully parsed transcripts do not incur an API call.
    foothill_layout = "Foothill-De Anza CCD" in text or "INSTITUTION CREDIT" in text
    candidate_starts = sum(1 for line in text.splitlines() if FH_ROW_START.match(line.strip()))
    simple_incomplete = foothill_layout and candidate_starts > len(courses)
    if use_claude_fallback and (not courses or simple_incomplete):
        ai_courses = parse_with_claude(text)
        if len(ai_courses) > len(courses):
            courses = ai_courses
            parser_used = "claude_fallback"

    if not courses:
        message = ("Text was extracted, but no course rows matched the simple parser. "
                   "Claude fallback was unavailable or could not parse this layout; add courses manually.")
    elif parser_used == "claude_fallback":
        message = ("The simple parser did not recognize this layout, so Claude extracted candidate rows. "
                   "Review every field before saving; extraction does not confirm equivalency.")
    else:
        message = (f"Extracted {len(courses)} candidate row(s) with the simple parser. "
                   + ("The simple parser detected a possible partial match; Claude fallback was unavailable or did not improve the result. " if simple_incomplete else "")
                   + "Review every field before saving; extraction does not confirm equivalency.")
    return {"text": text, "courses": courses, "needs_ocr": False,
            "parser_used": parser_used, "message": message}
