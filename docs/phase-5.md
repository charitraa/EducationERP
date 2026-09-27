# Phase 5 — Examinations

Exams, marks, grades, results, report cards and transcripts, for a school's
unit tests and terminals as much as a college's semester exams. Everything
follows the conventions in [`phase-1.md`](phase-1.md).

```
GradeScale (bands + divisions) ──────────────┐
                                              ▼
Exam ──► ExamSubject (paper) ──► ExamComponent (theory, practical, …)
  │           │                        │
  │           ├──► ExamRoom ──► SeatAllocation, Invigilation
  │           └──► MarkSheet ──► Mark ──► MarkCorrection
  │                                │
  ▼                                ▼
AdmitCard                  modules/examinations/results.py
                                    │
                                    ▼
                              Result ──► SubjectResult
                                    ▲
                      ResultPlan (several exams by weight)
```

Code: `backend/modules/examinations/`. `grading.py` holds the grading rules
as plain functions with no database access, so they're tested on their own;
`services.py` builds and schedules exams, seats candidates and takes marks;
`results.py` turns marks into stored results and publishes them; `selectors.py`
renders report cards, transcripts and summaries as data.

---

## Grading: one flexible scale, like Phase 3a's programs

A **GradeScale** belongs to one program, or to none (the organization's
default, used by every program without a scale of its own). It holds:

- **GradeBand**s: from a minimum percentage upwards, a letter, a grade point,
  a remark and pass/fail. One preset ships a common Nepali board-style table
  (`A+`…`NG`, 4.0 GPA, pass at 35%); another is percentage-only, classed into
  **DivisionBand**s (Distinction, First Division, …). Either is a starting
  point — check it against the actual circular before relying on it, and
  build your own scale if it differs.
- `require_all_subjects_pass`: one failed subject fails the whole result, the
  usual rule for schools. Off, plus an optional `overall_pass_percentage`,
  suits a credit system where a subject can be retaken later.

Bands are locked once a result made with them is published (`scale_in_use`),
so an edit can't silently move someone's grade after the fact. Make a new
scale for the next exam instead.

## An exam, end to end

1. **Create** the exam (campus, academic year, program, type, grade scale —
   defaulted from the program or the organization). It starts `draft`.
2. **Build its papers**: `POST /exams/{id}/add-curriculum/` adds one
   `ExamSubject` per curriculum subject at some levels, with a plain default
   component; or add papers one at a time with their own components (theory,
   practical, internal, …), each with its own full and pass marks. Editing
   components is refused once a paper has marks (`marks_entered`).
3. Give every paper a **date and time**. Checked against the calendar
   (`on_holiday`) and against the exam's other papers: two papers at the same
   level and slot clash unless they're electives nobody takes together
   (`paper_clash`, using the same sharing check as the timetable, Phase 3b).
4. **Schedule** it (`POST /exams/{id}/schedule/`): every paper needs a date,
   time and components, and none may clash. This sets the exam's span and
   writes one `CalendarEvent` (kind `exam`, `suspends_classes=True`) so the
   timetable and attendance leave those days alone — moving a paper afterwards
   (`reschedule_paper`) keeps both in step. It can go back to `draft`
   (`unschedule`) until marks exist.
5. Optionally **seat** candidates: add `ExamRoom`s (a capacity, or the room's
   own), then `POST /exams/{id}/seat-plan/` — `interleave` (default) puts
   neighbours from different classes, `sequential` keeps a class together;
   both refuse when there aren't enough seats (`not_enough_seats`, with the
   shortfall). `dry_run` previews without saving. Assign **Invigilation**s per
   room and paper; nobody can watch two rooms at the same time, in this exam
   or another (`invigilator_clash`).
6. Optionally **issue admit cards**: `POST /exams/{id}/generate-admit-cards/`
   makes one per candidate, running again only adds new ones. With
   `min_attendance_percent` set, anyone below it (from the academic year's
   start to the exam) gets a **withheld** card with the reason; the office can
   also withhold or release one by hand.
7. **Take marks** (below), then **publish**.

## Marks: teacher enters, office verifies, then publish

A **MarkSheet** is one paper's marks for one class — the same shape as
attendance's session (Phase 4): idempotent to open, `open → submitted →
verified`, and reopened only by the office.

- **Who may**: the paper's subject teacher for that class (`TeachingAssignment`
  says who), or anyone holding `exams.manage`. Checked on every action.
- **Who's expected**: everyone in the class on the paper's date who takes the
  subject — for an elective, only those who chose it (Phase 3a's
  `students_taking`), so a student's own choices and moves are what decide,
  the same rule attendance uses.
- **Enter marks**: `POST /mark-sheets/{id}/marks/` takes several students and
  components at once. A present student needs marks within the component's
  range; absent, exempt and withheld need none. While the sheet is open, this
  is free — no history is kept for a plain fix.
- **Submit**: needs everyone marked (`unmarked`, with who's missing) or an
  explicit status; once submitted, only the office may still change a mark,
  and must give a `reason` — the old value is kept as a `MarkCorrection`
  (mirroring attendance's `AttendanceCorrection`), and an audit entry is
  written. The office can also **send a sheet back** to the teacher with a
  reason, reopening it, but never once its exam is published.
- **Verify**: the office's sign-off that a sheet is ready to count towards
  results.

## From marks to a result

`grading.py`'s rules, given a scale and a subject's marks:

| Situation | Rule |
|---|---|
| A present student's percentage | Rounded half up to two places, at the subject level and again overall — so a recomputation is always identical |
| A component below its own pass mark | Fails the subject even if the overall percentage would pass (a common "must pass practicals" rule) |
| Absent | Counts as zero towards the subject (and the result), and is flagged so it prints as "Absent," not "0%" |
| Exempt | Left out of the subject/result entirely — the credit doesn't count either way |
| A component still unmarked | The subject (and result) is `incomplete`, never silently partial |
| Withheld | Holds the subject (and so the result) back until it's cleared |
| GPA | Credit-hour weighted average of each passing-or-failing subject's grade point |
| One failed subject | Fails the whole result, unless the scale says otherwise |
| Ranking | Competition style (1, 2, 2, 4): ties share a place; only passing students are ranked, per section and per level |

`compute_exam_results` (also `POST /exams/{id}/compute/`, a preview) computes
every candidate's result from the marks entered so far — safe to run
repeatedly. **Publishing** (`POST /exams/{id}/publish/`) additionally requires
every expected sheet to be verified (`GET /exams/{id}/readiness/` lists what
isn't) and every result complete; only then do students and parents see it.
**Unpublishing** needs a reason and puts the exam back to `scheduled`; the
stored results stay for staff.

A correction to a published exam's marks (`enter_marks` on a verified sheet,
with a reason) recomputes just that student's result — and any published
**term result** that counts the exam — rather than the whole exam, so a late
fix doesn't reshuffle everyone else's rank needlessly... except that ranking
*is* recomputed for everyone, since one student's new percentage can change
who's ahead of whom.

## Term results: several exams by weight

A **ResultPlan** combines exams into one result — e.g. two unit tests at 10%
each plus a terminal at 80%. Add its **exams and weights** (`ResultPlanExam`);
publishing needs every exam published first and the weights to total exactly
100%. Each subject is combined across the exams that had a paper in it: an
exam without the subject, or where the student was exempt, drops out and the
remaining weights fill the gap; an absence still counts at its full weight;
an unfinished or withheld part holds the subject back the same way a single
exam's component does.

## Report cards and transcripts: data, not PDFs

Everything a client needs to print — subjects, marks, letters, GPA, rank,
attendance over the covered span, and the grading table itself — comes back
as structured JSON (`selectors.report_card`, `GET /results/{id}/report-card/`,
or `GET /report-cards/?exam=&section=` for a whole class). Rendering to a PDF
is left to the web or mobile client. A **transcript**
(`GET /transcripts/{student_id}/`) lists every published result marked
`on_transcript`, oldest first, with a cumulative GPA across credits.

## Visibility

| Who | Sees |
|---|---|
| The exam office (`exams.manage`) | Everything at the campuses their role covers, published or not |
| A teacher (`exams.mark`) | Their own subjects' mark sheets; grade scales read-only |
| A student | `/exams/me/`, `/admit-cards/me/`, `/results/me/`, `/report-cards/me/`, `/transcripts/me/` — published results only, their own |
| A parent | The same `me` endpoints, for a linked child (`?student=` when there are several) |

## API surface

```text
GET    /api/v1/grades/scales/                                       CRUD; GET {id}/grade/?percentage=
GET    /api/v1/exam-types/                                          CRUD
GET    /api/v1/exams/                                                CRUD (draft only deletable)
POST   /api/v1/exams/{id}/add-curriculum/                           one paper per curriculum subject
POST   /api/v1/exams/{id}/schedule/  unschedule/                    draft ⇄ scheduled
GET    /api/v1/exams/{id}/readiness/                                 what blocks publishing
POST   /api/v1/exams/{id}/compute/                                    preview results, unpublished
POST   /api/v1/exams/{id}/publish/  unpublish/                       (reason required to unpublish)
GET    /api/v1/exams/{id}/summary/                                    pass rates, averages, toppers
POST   /api/v1/exams/{id}/seat-plan/  clear-seat-plan/                interleave | sequential
POST   /api/v1/exams/{id}/generate-admit-cards/                       withheld below min_attendance_percent
GET    /api/v1/exams/me/                                              a student's/parent's own exams
GET    /api/v1/exam-subjects/  exam-rooms/  seat-allocations/  invigilations/     CRUD (campus-scoped)
GET    /api/v1/admit-cards/                                           list/retrieve; GET {id}/data/
POST   /api/v1/admit-cards/{id}/withhold/  release/
GET    /api/v1/admit-cards/me/
GET    /api/v1/mark-sheets/                                           list/retrieve; POST opens one (idempotent)
GET    /api/v1/mark-sheets/mine/                                      a teacher's papers to mark
GET    /api/v1/mark-sheets/{id}/roster/                               who sits it, and marks so far
POST   /api/v1/mark-sheets/{id}/marks/  submit/  verify/  send-back/
GET    /api/v1/marks/                                                 list/retrieve; PATCH corrects (reason once locked)
GET    /api/v1/results/                                                list/retrieve (light list, full detail)
GET    /api/v1/results/{id}/report-card/
POST   /api/v1/results/{id}/remark/                                    class teacher's or office's note
GET    /api/v1/results/me/                                             published only; ?include_card=true
GET    /api/v1/report-cards/?exam=|plan=&section=|student=            a class's or a student's cards
GET    /api/v1/report-cards/me/
GET    /api/v1/transcripts/{student_id}/  transcripts/me/
GET    /api/v1/term-results/                                          CRUD; POST compute/  publish/  unpublish/
```

## Not built (by choice, for now)

- **PDF rendering.** Report cards and transcripts are data; a template
  belongs to the web or mobile client, or a later print service.
- **Re-sits and back exams** as their own concept. A retake is today just
  another exam or paper; nothing links it to the one it replaces, and a
  transcript lists both lines.
- **Grievances / re-checking requests.** A correction covers "the office
  changed a mark"; a student-initiated request-and-approval flow would sit
  with communication (Phase 8).
- **Notifications** ("your results are out"). Needs the Phase 8 notification
  service. `ExamResultPublished` is the event to publish.
- **Seat plan PDF / hall tickets as printable layouts** — the data
  (`seat-allocations`, `admit-cards/{id}/data/`) is there; the layout isn't.
