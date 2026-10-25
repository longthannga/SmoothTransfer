# SmoothTransfer

A community college transfer plan that fixes itself when life happens.

Built solo for Cal Hacks. Tracks: **Claude for Public Benefit** and **Bright Data**.

> **Important: SmoothTransfer is a suggestion tool. It cannot replace your counselor.** Course approvals, transfer rules and admission requirements change, and only a counselor can confirm what applies to you. Use this tool to understand your options and to walk into a counselor meeting prepared, then confirm the plan with them before you rely on it.

## Why I built this

About one in three students at the UCs and CSUs are transfer students. I am one of them. When I applied, I did not realize some schools would not accept my AP Physics credit, including UCLA and my safety choice. My last two quarters became 22 units each, while I worked full time. A tool could have shown me that months earlier.

SmoothTransfer is for the next student: especially first-generation students who plan alone and find out about a mistake too late.

## What it does

1. **Pick a community college, a university and a major.** The menus are filled from ASSIST's own list of institutions.
2. **Load the real requirements, live.** The app reads the public ASSIST agreement for that pair and shows which college course covers each university course.
3. **Add what you have done.** Upload a transcript PDF or type courses in. AP and IB credit is applied where the university's chart accepts it.
4. **Get a quarter-by-quarter plan** (or semester-by-semester, if your college uses semesters). Each row shows its units, your running total, and about how many hours a week it takes. Drag a course to another term to try a different order.
5. **When life happens, get options, not orders.** Type what changed ("I failed MATH 1A because I started a new job"). The app lists choices, such as cutting work hours, adding a summer term, or moving the goal term. You pick, and the plan is rebuilt.
6. **Plan general education (Cal-GETC) from your college's official list.** Courses are placed into areas for you. For open areas, Claude suggests one approved course each, using only courses on the list.

## How it uses Claude and Bright Data

**Bright Data.** ASSIST gives its data out only through a token the site creates when a page loads. The app opens the real ASSIST page in Bright Data's Browser API and captures the data the page requests. This covers majors, agreements, the institution list and each college's Cal-GETC list. Everything is public, with no logins and no mock data.

**Claude plans, Python checks.** Claude orders the courses, balances each term, explains the plan and offers options. Plain Python then verifies it before anyone sees it:

- every course must be in the agreement
- no course is repeated
- no term is over your unit limit
- every requirement is met, including "pick N" rules
- Cal-GETC suggestions must be on that area's official list and cannot be used twice

A plan that fails the check goes back to Claude to be fixed.

## Run it

You need Python 3.11 or newer.

```bash
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install streamlit pandas anthropic playwright python-dotenv streamlit-sortables pypdf requests
streamlit run app.py
```

Create a `.env` file next to `app.py`:

```
ANTHROPIC_API_KEY=your-anthropic-key
BROWSER_WS=wss://your-bright-data-browser-api-address
```

Optional settings: `SENDING_ID`, `RECEIVING_ID`, `SENDING_NAME`, `RECEIVING_NAME` and `YEAR_ID` set the default colleges and year (the numbers come from assist.org's web address). You can also change them in the app.

Never commit `.env`. It holds your keys.

Run the tests with `python -m unittest` (or run a single `test_*.py` file).

## Files worth knowing

| File | What it does |
|---|---|
| `app.py` | The Streamlit app |
| `assist.py` | Live ASSIST fetching through Bright Data, with retries |
| `requirements_parser.py` | Turns an ASSIST agreement into clean requirements |
| `planner.py`, `validator.py` | Claude builds the plan, code checks it |
| `institutions.py`, `terms.py`, `termwords.py` | Any college and university, quarters or semesters |
| `calgetc_api.py`, `calgetc_list.py`, `cal_getc_board.py` | Cal-GETC lists, matching and the placement board |
| `gc_suggest.py` | Claude's checked Cal-GETC course suggestions |
| `external_exams.py`, `ap.py` | AP and IB credit |
| `LIMITATIONS.md` | What it checks, where the data comes from, and what it does not do |
| `IMPACT_PLAN.md` | How benefit will be measured |

## Future improvements

The next step is to take the plan from "which courses, which term" down to "what do I do this week".

- **Week-by-week study plans.** For each term, Claude builds a weekly plan from the student's own information:
  - their **available hours** each week, after work, family and other commitments
  - their **uploaded course materials**, such as the syllabus, schedule and assignment lists, so the plan follows real due dates and exams
  - their **transcript**, so harder or weaker subjects get more time
- **Checked like the transfer plan.** Code would verify that the weekly hours add up, never go over what the student has free, and cover every exam and deadline. The student gets options when a week falls apart, as they do today for a term.
- **Adjust as the term goes.** If a student falls behind, the weeks that remain are replanned instead of starting over.
- **More universities' AP and IB charts**, and a transcript reader for more colleges.
- **Fuller UC and CSU transferability checks**, including credit limits and duplicate-credit rules.
- **Feedback from community college students**, to see what helped and what confused them, and fix it.

## Honest limits

SmoothTransfer is a **planning aid, not an official evaluation**. Always confirm with a counselor. Main limits:

- UC transferability is only partly checked.
- Foothill to UC San Diego is the pair run most against live ASSIST. Other colleges were tried live in a few cases and mostly tested with data copied from ASSIST's own responses.
- The transcript reader supports Foothill and De Anza formats. The AP and IB charts are UC San Diego's.
- Course lists change every year, so check the year the list covers.

The full list is in `LIMITATIONS.md`. No results are claimed. The impact plan describes how benefit will be measured, starting with asking community college students for feedback.

## Privacy

Plans and course records stay in a local SQLite file (`smooth.db`). A profile code works like a private link. Nothing identifying is collected by the feedback form.

## Author

Hoang Long, a first-generation computer science transfer student at UCSD. I want to build AI tools that make services easier to reach for under-resourced communities.
