# Phase 3b — Timetable

When each class meets, who teaches it, and where. Built on the Phase 3a
structure ([`phase-3.md`](phase-3.md)): the timetable schedules **teaching
assignments** (section + subject + teacher). Attendance (Phase 4) is taken
against its entries.

Module: `backend/modules/timetable/`. Follows the conventions in
[`phase-1.md`](phase-1.md) and [`phase-2.md`](phase-2.md).

---

## Data model

```
Campus
  └── BellSchedule  "Day shift", "Morning (+2)"         name unique per campus
        └── Period  "Period 1" 10:00–10:45 | "Lunch" (is_break)
                    no overlap inside one schedule; end > start

TimetableEntry
  teaching_assignment  → section, subject, teacher
  day_of_week          1 = Monday … 7 = Sunday (ISO, as date.isoweekday())
  period               a period of the section's campus, not a break
  room?                defaults to the section's home room; same campus
  term?                empty = the whole academic year; else that term only
  combined_group?      entries sharing it are one combined class
  valid_from?          first day it runs (a lesson added mid-year: today)
  valid_until?         last day it runs (set when it's changed or removed)

LessonChange          one lesson on one date
  entry, date          the date must be one the lesson takes place on
  substitute_teacher?  | room?  | is_cancelled
```

A campus can have several bell schedules. Nepali +2 colleges often run a
morning shift and a day shift with different bells, and one school may
have a different bell for Friday. Any section can use any schedule of its
campus.

Weekdays aren't configured per institution. A school that doesn't open on
Saturday simply has no Saturday entries.

---

## Clash detection

A new or changed entry is compared with every live entry of the **same
academic year**, on the **same weekday**, whose period **overlaps in clock
time**, and whose term can run at the same time (an all-year entry overlaps
every term; entries of two different terms never clash). It clashes if they
share:

| Shared | Clash? |
|---|---|
| Teacher | Always, **at any campus**. A person can't be in two places. |
| Room | Always |
| Section | Yes, unless **both** subjects are electives at that level and **no student of the section takes both** (see student electives in [`phase-3.md`](phase-3.md)). Electives run in parallel for different groups of students. |

Lessons of one combined class share their teacher and room by design, so
they never clash with each other. A weekly lesson also clashes with an
**upcoming substitution** (from today on) that already has the teacher or
room busy at that weekday and time.

Overlap is by clock time, not by period, so the morning shift's 10:30–11:15
clashes with the day shift's 10:00–10:45. Back-to-back periods (10:00–10:45,
10:45–11:30) don't clash.

A clash is refused with **409**:

```json
{"error": {
  "code": "timetable_clash",
  "message": "Clashes with Physics for Grade 11 A (10:00–10:45): same teacher.",
  "details": {"clashes": [
    {"kind": "teacher", "entry": 12, "section": "Grade 11 A", "campus": "Lalitpur",
     "subject": "Physics", "teacher": "Hari Sharma", "room": "Room 101",
     "period": "Period 1", "start_time": "10:00", "end_time": "10:45"}
  ]}
}}
```

A clash at a campus the caller can't see (for example, a teacher who also
teaches at another branch) shows only `kind`, `campus`, `start_time` and
`end_time`. The admin learns the teacher is busy, but doesn't see the other
branch's timetable.

### Race safety

"Times overlap" can't be written as a portable database constraint (MySQL
has no exclusion constraints). Instead, each write runs in one transaction:

1. Lock the section, teacher and room rows (`SELECT … FOR UPDATE`, always
   in that order, so two writes can't deadlock).
2. Look for clashes.
3. Save.

Two admins booking the same teacher at the same moment are serialized, so
the second one sees the first one's lesson. The database also rejects exact
duplicates (same assignment, day, period and term) with a partial unique
constraint.

---

## Rules and where they're enforced

| Rule | Serializer (400) | Service (409) | Database |
|---|:-:|:-:|:-:|
| Bell schedule name unique per campus; can't move campus | ✓ | | partial unique |
| Period ends after it starts | ✓ | | check |
| Periods of one schedule don't overlap | ✓ | | |
| Period times and break flag locked once lessons use it | ✓ | | |
| No lessons in a break | ✓ | | |
| Period and room at the section's campus | ✓ | | |
| Term belongs to the section's academic year | ✓ | | |
| Teacher who has left can't be scheduled | ✓ | | |
| A lesson moves only to another assignment of the **same section** | ✓ | | |
| Same lesson twice in one slot | ✓ | | partial unique |
| Teacher, room or section double-booked | | ✓ | row locks |
| Deleting a period, schedule, room, term or teaching assignment with live lessons | | 409 `in_use` | PROTECT |

### Guards added to academics

Once a record is on the timetable, academics refuses changes that would
break it without anyone noticing:

- A **teaching assignment's teacher** can't be swapped, because the new
  teacher's week was never checked. Use
  [hand-over](#handing-lessons-over) instead, which checks every lesson.
- A **section's campus or academic year** can't change. Its lessons use that
  campus's periods and rooms.
- **Rooms, terms and teaching assignments** in use can't be deleted (409).

---

## Permissions

| Permission | campus-admin | staff |
|---|:-:|:-:|
| `timetable.view` | ✓ | ✓ |
| `timetable.manage` (bell schedules, periods, lessons) | ✓ (own campus) | |

`org-admin` holds everything. Campus scoping: bell schedules by `campus`,
periods by `schedule.campus`, lessons by `teaching_assignment.section.campus`.
The campus check runs **before** clash detection, so writing to another
campus is always a 403, never a 409 that reveals that campus's timetable.

---

## API

```
/api/v1/bell-schedules/   CRUD   ?campus=  (delete also removes its periods)
/api/v1/periods/          CRUD   ?schedule=  one schedule's day, in time order
/api/v1/timetable/        CRUD   409 timetable_clash on a double booking
    ?section=<id>     one class's week
    ?teacher=<id>     one teacher's week, across sections
    ?room=<id>        one room's week
    ?date=YYYY-MM-DD  the lessons of one day: its weekday, inside the section's
                      academic year and, for term lessons, inside the term
    also: ?subject= ?campus= ?academic_year= ?term= ?period= ?day_of_week= ?combined_group=
    The list shows the timetable from today on; ?include_ended=true adds history.
    PATCH takes effective_from; DELETE takes ?effective_from= (see "History").
/api/v1/timetable/day/?date=      one day with its changes and the calendar applied
/api/v1/bell-schedules/{id}/retime/  POST  new bell times from a date (winter timings)
/api/v1/timetable/me/             the caller's own timetable: teacher, student or parent
/api/v1/timetable/hand-over/      POST  move assignments' lessons to another teacher
/api/v1/timetable/generate/       POST  fill the week from periods_per_week (dry run by default)
/api/v1/lesson-changes/           CRUD  substitutes, room changes, cancellations on a date
```

Entries return the section, subject, teacher, period times, room and term
by name, so a client can draw the grid from the list alone. Ordered by
weekday, then start time. The list runs a constant number of queries
regardless of row count.

---

## Combined classes

One teacher teaching several sections together, in one room, at one time.
For example, Physics for 11 A and 11 B in the hall.

```
POST /api/v1/timetable/ {"teaching_assignment": <11 B physics>, "combine_with": <11 A's lesson>}
```

- The lesson takes its day, period, room and term from the class it joins.
  Giving different ones is a 400.
- The joining assignment must have the **same teacher and subject**, and its
  section must be at the same campus, in the same academic year, and not
  already in the class.
- The joining section must be free at that time (clash check).
- **Moving** any lesson of the class (day, period, room, term) moves them
  all. Each section is checked at the new time; one clash means nothing moves.
- **Deleting** a section's lesson takes that section out of the class. When
  one section is left, it's an ordinary lesson again.
- The teacher changes only through **hand-over**, for every section at once.

## Handing lessons over

`POST /api/v1/timetable/hand-over/ {"teaching_assignments": [...], "teacher"}`
gives every lesson of those assignments to another teacher. Use it when a
teacher leaves mid-year or subjects are reshuffled.

- For each assignment, the new teacher gets one for the same section and
  subject (created if needed, keeping `periods_per_week`), and the lessons
  move over.
- The old assignments stay, without lessons, as a record of who taught before.
- The move is checked like any lesson: against the new teacher's week, and
  among the moving lessons (two lessons at the same time can't both go to
  one person). All or nothing: 409 `timetable_clash` lists every clash.
- A combined class must be handed over whole (409 `combined_class`).
- Needs `timetable.manage` and `academics.manage_classes`, since it creates
  assignments.

## Lesson changes: substitutes, room changes, cancellations

```
/api/v1/lesson-changes/   CRUD   ?date= ?date_from= ?date_to= ?section= ?teacher= ?substitute_teacher=
                                 ?room= ?entry= ?is_cancelled=
{"entry", "date", "substitute_teacher"?, "room"?, "is_cancelled"?, "note"?}
```

A change alters one lesson on one date. The weekly entry itself stays as
it is.

- The date must be one the lesson takes place on: its weekday, inside the
  academic year and, for a term lesson, inside the term.
- A change must do something: a substitute, another room, or a
  cancellation. A cancellation has neither a substitute nor a room.
- The substitute can't be the lesson's own teacher, and can't be someone
  who has left. The room must be at the same campus and in use.
- A lesson has one change per date. Edit that change rather than adding a
  second one. The lesson and date of a change can't be edited.
- **The substitute or room must be free then**, with that day's changes
  applied:
  - a teacher whose own lesson is cancelled, or covered by someone else,
    is free;
  - a teacher already covering another lesson at that time is busy.

  If not, the response is 409 `timetable_clash` with the date.

### The day view

`GET /api/v1/timetable/day/?date=YYYY-MM-DD&section=|teacher=|room=` lists the
lessons of one day with its changes applied:

- `teacher` is the teacher who actually takes the lesson, and
  `regular_teacher` is the one on the weekly timetable.
- A teacher's day includes the lessons they cover, and leaves out the ones
  someone else covers for them.
- A room's day includes the lessons moved into it.
- Cancelled lessons are listed with `is_cancelled: true`, so the day can
  show them struck out.

Attendance (Phase 4) builds on this view.

## My timetable

`GET /api/v1/timetable/me/` needs no timetable permission, like the other
`/me/` endpoints.

| Profile | Shows |
|---|---|
| Teacher (staff record linked to the account) | Their lessons. With `date`, the lessons they cover too |
| Student | Their current class's lessons: compulsory subjects and the electives they take. Every elective shows until a choice is recorded |
| Parent | A linked child's, as the student would see it. `?student=` is required when there are several children |

Without `date`, it returns the week's entries. With `?date=YYYY-MM-DD`, it
returns that day's lessons with changes applied, in the day-view shape. An
account with several profiles picks one with `?as=teacher|student|parent`;
the default is the first found, in that order.

## Generating the timetable

```
POST /api/v1/timetable/generate/
{"sections": [...], "schedule": <bell schedule>, "days": [7, 1, 2, 3, 4, 5],
 "term"?: <term>, "dry_run": true}
```

It fills each teaching assignment of those sections up to its
`periods_per_week`, using the bell schedule's periods on the given days.
`days` are ISO numbers, so Sunday–Friday is `[7, 1, 2, 3, 4, 5]`.

- **`dry_run` is the default.** It returns the plan and saves nothing. With
  `"dry_run": false` the plan is saved (201). Each lesson is checked again
  under row locks, just like a hand-made one.
- **Lessons already on the timetable count.** Generating again only fills
  gaps, and a partly hand-made timetable can be finished automatically.
- **Greedy, in rounds.** Each round gives every assignment that still needs
  lessons one more, so no subject is left for last. Each lesson takes the
  free slot that best:
  1. spreads the subject over the week;
  2. puts electives beside other electives of the section, so students
     split up for them;
  3. balances the section's days;
  4. uses early periods and early days.
- **Free** means the teacher (at any campus), the section and a room are
  all free, by the same rules as clash detection. Lessons go in the
  section's home room. A parallel elective takes the campus's first free
  room instead.
- **Deterministic.** The same input gives the same timetable.
- **What can't be placed is reported** in `unplaced`, with how many lessons
  are missing. It is never forced.

The sections must be at the schedule's campus, and a `term` must be of
their academic year. Needs `timetable.manage` at that campus.

---

## History: the past stays true

Lessons and bell times carry `valid_from` and `valid_until`. A change to a
lesson that has already run is never an edit in place:

- **PATCH** a lesson's teacher (same section), day, period, room or term,
  with `effective_from` (default today). If the lesson ran before that date,
  it ends the day before and a new version carries on (the response has the
  new id). If it hasn't started, it's edited in place.
- **DELETE** a lesson that ran ends it the day before `?effective_from=`
  (default today). Its future lesson changes are removed. A lesson that
  never ran is simply removed.
- **Hand-over** takes `on` (default today). Past lessons keep the teacher
  who taught them. The old assignment is marked inactive and gets no new
  lessons.
- **Retime** (`POST /bell-schedules/{id}/retime/ {"effective_from", "periods":
  [{"period", "start_time", "end_time"}]}`) moves periods and their lessons
  to new clock times from a date. The new times must not overlap the rest
  of the day, and moved lessons are checked against those that aren't
  moving (another shift, a shared teacher). All or nothing.
- **Planned substitutes** follow a new version when it still meets that
  day. Moving a lesson to another weekday with planned changes is refused
  (409 `planned_changes`).
- A lesson **added mid-year** runs from today, not retroactively.
- Clashes are checked over the dates both lessons actually run. A slot
  freed from next Monday is free from then, not before.

The list shows the timetable from today on; `?include_ended=true` shows the
history, and `?date=` any day.

## The academic calendar

`/api/v1/calendar/` (in academics) holds holidays, closures, exam days,
events and make-up days, optionally for one campus, one program or one
grade. The day view, `/me/` and lesson changes read it:

- On a **holiday, closure or exam day**, the lessons it covers show
  `is_cancelled: true` and `closed_by: "Dashain"`. A lesson change on such
  a day is refused.
- An **event** doesn't stop classes, unless `suspends_classes` is set.
- A **make-up day** (`runs_timetable_of`, 1–7) runs another weekday's
  timetable for the sections it covers.
- A campus admin manages their campus's events and sees events for every
  campus. Events for every campus need an organization-wide role
  (`academics.manage_calendar`).

## Teachers per subject, and room size

- A teaching assignment has a `role`: `lecture`, `practical`, `tutorial` or
  `co_teaching`. One subject can have theory with one teacher and a lab with
  another.
- **Practical groups** of one subject may meet at the same time in
  different rooms. A **co-teacher** may share the time and room. Anything
  else in the same section at that time is a clash.
- **Room size.** Booking a room with fewer seats than the class is refused
  (409 `room_too_small`) unless `allow_over_capacity` is sent. A combined
  class counts every section. An elective counts only the students who
  take it.

## Combined classes and lesson changes

A substitute, room change or cancellation on one section of a combined
class applies to every section. Editing or removing it follows too, so one
hall never has two teachers.

---

## Tests

130 tests for the timetable (513 in the suite). They cover every rule
above, each kind of clash, and the real-life situations listed in
[`phase-3-real-life.md`](phase-3-real-life.md).

---

## Limits

- **The generator is greedy, not a solver.** It never produces a clash, but
  a very tight week (every teacher nearly full) can leave lessons unplaced
  that a backtracking solver might fit. Place those by hand, or free a
  slot and generate again.
- **Electives carried over on a same-level move aren't re-checked** against
  the new section's timetable.
- **The generator** doesn't know teacher availability, labs or double
  periods. See [`phase-3-real-life.md`](phase-3-real-life.md).
