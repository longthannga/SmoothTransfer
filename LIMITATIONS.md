# What SmoothTransfer checks, where the data comes from, and what it does not do

**SmoothTransfer is a suggestion tool. It cannot replace your counselor.** It is a planning aid, not an official evaluation. Course approvals and transfer rules change, and only a counselor can confirm what applies to you. Use it to understand your options and to prepare for a counselor meeting, and always confirm your plan with a counselor.

## Where each answer comes from
| Answer | Source | How it is checked |
|---|---|---|
| Major requirements and which community college course covers each | ASSIST agreement, fetched live through Bright Data's Browser API | Parsed by code. Courses with no equivalent are shown as "No Course Articulated". Sections the parser cannot read are left out and the app says how many |
| The list of colleges and universities | ASSIST's institution list (`isCommunityCollege` true = sending school, false = receiving school) | Read by code, with a short built-in list as a backup |
| AP and IB credit for the major | UCSD's AP and IB charts, linked from the agreement | Read by Claude, then every rule is checked against the PDF's own text. Rules that cannot be matched are not applied unless you choose to |
| Which courses count for which Cal-GETC area | ASSIST's Cal-GETC list for the student's own college, with Foothill's published PDF as a backup (Foothill only) | Read by code. A course counts only if it was approved when it was taken |
| AP and IB exams that count for a Cal-GETC area | Cal-GETC Standards v1.3, section 6 | Table copied into `external_exams.py`. AP needs 3+, IB Higher Level needs 5+. One area per exam. Science AP exams also meet the lab |
| Cal-GETC area counts | Cal-GETC Standards v1.3 and several colleges' official 2025-26 sheets | 11 courses, 34 semester units. Area 3 is two courses, Area 4 is two from different disciplines, Area 5 needs a lab |
| Suggested Cal-GETC courses | Claude, choosing only from the college's official list for that area | Code drops any suggestion that is not on that area's list or is used twice |
| The order of terms and the workload | Claude | Code then checks unit limits, minimums, the transfer date, summer terms, prerequisite order, and that every required course is scheduled |

## What it does not do
- **UC transferability is only partly checked.** Units are split into "confirmed UC-transferable" (the course is in ASSIST's agreement for the major) and "not confirmed". Most college courses transfer, but nothing here checks the UC transferable course list, UC's credit limits, or duplicate-credit rules.
- **AP and IB units** are not counted toward the 90-quarter-unit minimum.
- **Foothill to UC San Diego is the pair tested most.** Any community college (the schools ASSIST marks `isCommunityCollege`) can be picked as the sending school, and any university that is not one (UC and CSU campuses) as the receiving school. A few other colleges and universities were run live (Canada, Bakersfield, Vista, Berkeley City, UC Berkeley), but most pairs were not, so expect rough edges. Quarter and semester colleges are both supported, and the plan uses the sending college's system. Specifically:
  - ASSIST's `termType` numbers are read as 0 = semester and 1 = quarter. That is inferred from the data (UC San Diego is 1, and Cal Poly San Luis Obispo changes from 1 to 0 in 2026), not from ASSIST documentation.
  - A semester college plans Fall and Spring, with Summer optional. Winter is a short intersession there, so it is not planned.
  - Cal-GETC placeholders count as 3 units at a semester college (they may be 3 to 4), and the study-hours rule is the same 4 hours a week per unit.
  - Still specific to Foothill and UC San Diego: the transcript reader (Foothill and De Anza formats), the AP and IB charts (UCSD's), and the backup Cal-GETC PDF (Foothill's, never shown for another college). The 60-semester-unit (90 quarter unit) minimum is stated for UC only.
- **Some agreements have section layouts the parser does not read yet.** When that happens the app shows a warning with the number of sections or courses it left out. Check those on assist.org. A plan built from a partly read agreement can miss requirements.
- **The Cal-GETC course list may not load.** It is captured from ASSIST's own page for the college. If ASSIST changes that page or a college has no list, the app says so and the board works by hand. Foothill is the only college with a backup PDF.
- **ASSIST can be slow.** Each fetch is tried up to three times. If it still fails, wait a minute and try again. Agreements already loaded are cached for a week.
- **Claude's Cal-GETC suggestions are a starting point.** They are limited to approved courses, but Claude does not know which sections are offered, which course a counselor would prefer, or your campus's own rules. Each card only offers Claude the first 80 courses on its area's list.
- **The science "pick" rule is not independently confirmed.** If ASSIST counts a section in something other than courses (for example a series), the app shows a warning. `python explain_sections.py "<major key>"` prints how each section is read so it can be compared with assist.org.
- **Cal-GETC placeholders** count as 5 units (quarter) or 3 units (semester) until you pick a real course from the approved list. A course that is not on the list stays an estimate.
- **Course lists change every year.** The default list is for 2025-26 and the app warns when a plan starts after the year the list covers. Cal-GETC Standards v1.3 should be rechecked against the current version.
- **Not tested everywhere.** Dragging courses between terms was checked in Chrome. Safari is untested.
- **Self-reported results.** The impact plan describes how benefit will be measured. No results are claimed.

## Privacy
Course records and plans are saved in a local SQLite file (`smooth.db`). A profile code works like a private link. Nothing identifying is collected by the feedback form. Public ASSIST data is cached in the same file. API keys live only in your `.env` file.
