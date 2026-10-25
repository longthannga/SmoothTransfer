# SmoothTransfer — implementation pass 1

## What is implemented

- SQLite-backed saved profiles, including the selected major, completed ASSIST courses, plan, schedule limits, and goals.
- Seven-day cache for ASSIST major lists and articulation agreements.
- Shared academic-record storage for manually entered course records and AP/IB exam scores.
- Create an academic-record profile before building a plan, so transcript uploads, manual courses, AP/IB scores, and Cal-GETC progress can be saved independently of plan generation.
- PDF text extraction with an editable review step. The deterministic parser handles Foothill-De Anza transcript formatting, including wrapped titles, split `C S` subject text, transfer/AP-credit rows, terms, and in-progress courses. Extracted classes are saved as **Needs review**; they are not automatically considered transferable.
- Optional Claude fallback when the simple parser finds no rows or detects a likely partial parse. It requires `ANTHROPIC_API_KEY`; Claude output remains unverified and must be reviewed. The simple parser is used without an API call when it successfully parses the transcript.
- A Cal-GETC area checklist with saved progress and a reminder to verify course approval against the student's college and catalog year.
- A safer startup path when `BROWSER_WS` is missing: the app can load and show a helpful error when the user tries to fetch live ASSIST data.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in BROWSER_WS and ANTHROPIC_API_KEY
streamlit run app.py
```

The planner also needs the API credentials used by the existing `planner.py` configuration. Keep credentials in `.env` or environment variables; never commit them.

## Persistence notes

The app writes to `smooth.db` beside `store.py`. This is persistent on a normal local machine. On a hosting platform with an ephemeral filesystem, configure a persistent disk or move the database to managed storage.

Use **Create profile for my academic records** in the Saved profile section before uploading a transcript or adding courses; a plan is not required. The profile code behaves like a private link, not an authenticated account. Anyone who knows the code can load that profile. Do not store sensitive documents or expose profile codes publicly.

## Current limitations / next pass

- PDF extraction handles selectable text. Scanned PDFs need OCR. Unusual layouts can invoke the optional Claude fallback, but all extracted rows still require manual review.
- AP/IB scores are recorded, but official score-to-credit rules are not yet applied automatically.
- Cal-GETC progress is a checklist, not an official certification engine. Verify every course and score with the current official articulation/approved course list and a counselor.
- Manual course records are not automatically treated as equivalent to ASSIST courses. In particular, a course from another college must be reviewed before it satisfies a requirement.
- Authentication, multi-user authorization, transcript-file retention/deletion controls, and cloud database deployment are not implemented.

## Tests

```bash
python -m unittest test_store.py test_transcript_parser.py -v
python -m py_compile app.py assist.py store.py transcript_parser.py cal_getc.py
```
