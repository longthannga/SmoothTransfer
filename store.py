"""SQLite persistence for SmoothTransfer.

Profiles are identified by an unguessable share code, not by a login. Anyone who
knows a code can open that profile, so treat it like a private link.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).with_name("smooth.db")
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

def _init_db() -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS agreements(key TEXT PRIMARY KEY, fetched_at REAL, data TEXT);
    CREATE TABLE IF NOT EXISTS profiles(
      code TEXT PRIMARY KEY, major_key TEXT, major_label TEXT, completed TEXT,
      max_units INTEGER, start_term TEXT, goal_term TEXT, notes TEXT,
      plan TEXT, updated_at REAL
    );
    CREATE TABLE IF NOT EXISTS academic_courses(
      id INTEGER PRIMARY KEY AUTOINCREMENT, profile_code TEXT NOT NULL,
      course_code TEXT NOT NULL, title TEXT DEFAULT '', institution TEXT DEFAULT '',
      term TEXT DEFAULT '', units REAL, status TEXT NOT NULL DEFAULT 'Needs review',
      source TEXT NOT NULL DEFAULT 'manual', notes TEXT DEFAULT '', created_at REAL,
      UNIQUE(profile_code, course_code, institution, term),
      FOREIGN KEY(profile_code) REFERENCES profiles(code) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS exam_credits(
      id INTEGER PRIMARY KEY AUTOINCREMENT, profile_code TEXT NOT NULL,
      exam_type TEXT NOT NULL, subject TEXT NOT NULL, score TEXT NOT NULL,
      year INTEGER, status TEXT NOT NULL DEFAULT 'Needs official mapping', notes TEXT DEFAULT '',
      created_at REAL,
      FOREIGN KEY(profile_code) REFERENCES profiles(code) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS cal_getc_progress(
      profile_code TEXT NOT NULL, area_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Not reviewed',
      course_notes TEXT DEFAULT '', updated_at REAL,
      PRIMARY KEY(profile_code, area_id),
      FOREIGN KEY(profile_code) REFERENCES profiles(code) ON DELETE CASCADE
    );
    """)
    # Old databases may have been created before the academic-record tables existed.
    db.commit()

_init_db()


def cache_agreement(key: str, data: Any) -> None:
    db.execute("INSERT OR REPLACE INTO agreements(key, fetched_at, data) VALUES (?,?,?)",
               (key, time.time(), json.dumps(data)))
    db.commit()


def get_agreement(key: str, max_age_days: int = 7):
    row = db.execute("SELECT fetched_at, data FROM agreements WHERE key=?", (key,)).fetchone()
    if row and time.time() - row["fetched_at"] < max_age_days * 86400:
        return json.loads(row["data"]), row["fetched_at"]
    return None, None


def cache_majors(key: str, data: Any) -> None:
    cache_agreement("majors:" + key, data)


def get_majors(key: str, max_age_days: int = 7):
    return get_agreement("majors:" + key, max_age_days)


def save_profile(code: str | None = None, **fields):
    """Create/update a profile. Returns its stable code."""
    code = code or secrets.token_urlsafe(9)
    existing = db.execute("SELECT code FROM profiles WHERE code=?", (code,)).fetchone()
    defaults = {
        "major_key": None, "major_label": None, "completed": [], "max_units": 16,
        "start_term": "Winter 2027", "goal_term": "Fall 2028", "notes": "", "plan_res": None,
    }
    defaults.update(fields)
    plan = defaults.get("plan_res")
    db.execute("""INSERT INTO profiles
      (code, major_key, major_label, completed, max_units, start_term, goal_term, notes, plan, updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(code) DO UPDATE SET major_key=excluded.major_key,
      major_label=excluded.major_label, completed=excluded.completed,
      max_units=excluded.max_units, start_term=excluded.start_term,
      goal_term=excluded.goal_term, notes=excluded.notes, plan=excluded.plan,
      updated_at=excluded.updated_at""",
      (code, defaults["major_key"], defaults["major_label"], json.dumps(defaults["completed"]),
       int(defaults["max_units"]), defaults["start_term"], defaults["goal_term"],
       defaults["notes"], json.dumps(plan) if plan is not None else None, time.time()))
    db.commit()
    return code


def load_profile(code: str):
    row = db.execute("SELECT * FROM profiles WHERE code=?", (code.strip(),)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["completed"] = json.loads(result["completed"] or "[]")
    result["plan"] = json.loads(result["plan"]) if result["plan"] else None
    return result


def list_profiles(limit: int = 50):
    rows = db.execute("SELECT code, major_label, updated_at FROM profiles ORDER BY updated_at DESC LIMIT ?",
                      (limit,)).fetchall()
    return [dict(r) for r in rows]


def add_course(profile_code: str, *, course_code: str, title: str = "", institution: str = "",
               term: str = "", units: float | None = None, status: str = "Needs review",
               source: str = "manual", notes: str = "") -> int:
    if not course_code.strip():
        raise ValueError("Course code is required")
    allowed = {"Verified", "Needs review", "Unmapped"}
    if status not in allowed:
        raise ValueError(f"Status must be one of {sorted(allowed)}")
    cur = db.execute("""INSERT INTO academic_courses
      (profile_code, course_code, title, institution, term, units, status, source, notes, created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?)""",
      (profile_code, course_code.strip(), title.strip(), institution.strip(), term.strip(), units,
       status, source, notes.strip(), time.time()))
    db.commit()
    return int(cur.lastrowid)


def list_courses(profile_code: str):
    return [dict(r) for r in db.execute(
        "SELECT * FROM academic_courses WHERE profile_code=? ORDER BY id", (profile_code,)).fetchall()]


def delete_course(profile_code: str, course_id: int) -> None:
    db.execute("DELETE FROM academic_courses WHERE profile_code=? AND id=?", (profile_code, course_id))
    db.commit()


def add_exam_credit(profile_code: str, *, exam_type: str, subject: str, score: str,
                    year: int | None = None, status: str = "Needs official mapping", notes: str = "") -> int:
    if exam_type not in {"AP", "IB"}:
        raise ValueError("Exam type must be AP or IB")
    if not subject.strip() or not str(score).strip():
        raise ValueError("Exam subject and score are required")
    cur = db.execute("""INSERT INTO exam_credits
      (profile_code, exam_type, subject, score, year, status, notes, created_at)
      VALUES (?,?,?,?,?,?,?,?)""",
      (profile_code, exam_type, subject.strip(), str(score).strip(), year, status, notes.strip(), time.time()))
    db.commit()
    return int(cur.lastrowid)


def list_exam_credits(profile_code: str):
    return [dict(r) for r in db.execute(
        "SELECT * FROM exam_credits WHERE profile_code=? ORDER BY id", (profile_code,)).fetchall()]


def set_cal_getc_progress(profile_code: str, area_id: str, status: str, course_notes: str = "") -> None:
    db.execute("""INSERT INTO cal_getc_progress(profile_code, area_id, status, course_notes, updated_at)
      VALUES (?,?,?,?,?) ON CONFLICT(profile_code, area_id) DO UPDATE SET
      status=excluded.status, course_notes=excluded.course_notes, updated_at=excluded.updated_at""",
      (profile_code, area_id, status, course_notes, time.time()))
    db.commit()


def get_cal_getc_progress(profile_code: str):
    return {r["area_id"]: dict(r) for r in db.execute(
        "SELECT * FROM cal_getc_progress WHERE profile_code=?", (profile_code,)).fetchall()}
