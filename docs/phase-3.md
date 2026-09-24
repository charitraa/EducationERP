# Phase 3a — Academic Structure

What is taught, when, where and to which groups, and which group each
student is in. It also records students' electives and promotes whole
classes. Phase 3b adds the timetable on top of this: see
[`phase-3b.md`](phase-3b.md).

Follows the conventions in [`phase-1.md`](phase-1.md) and
[`phase-2.md`](phase-2.md).

---

## One model for schools, +2 colleges and universities

A **Program** has a numbered range of **levels**; a **Section** is one
teaching group at one level of one program, for one academic year, at one
campus.

| Institution | Program | Levels | A section |
|---|---|---|---|
| School | "Secondary School" | Grade 1–10 | Grade 5 A, 2082/83, Main Campus |
| +2 college | "+2 Science", "+2 Management" | Grade 11–12 | Grade 11 B, 2083/84, Baneshwor |
| University | "BSc CSIT" | Semester 1–8 | Semester 3 A, 2083/84, Lalitpur (batch "CSIT 2082") |
| Montessori | "Montessori" | Grade 0–0 | Grade 0 Sunflower |

Dates are stored in AD. Names are free text, so Bikram Sambat labels such as
`2082/83` work as they are.

---

## Data model

```
Organization-wide (academics.manage_structure)
  Department ─── head → StaffMember
    ├── Program (level_type, first_level..last_level)
    │      └── CurriculumSubject: Subject taught at a level (optional: elective)
    └── Subject (credit hours)
  AcademicYear (one is_current) ── Term (sequence, inside the year, no overlap)

Campus-level (academics.manage_classes, campus-scoped)
  Room (code unique per campus, type, capacity)
  Batch: an intake cohort of a program at a campus, e.g. "BSc CSIT 2082"
  Section: year + campus + program + level + name
     ├── batch?        (same program and campus)
     ├── class_teacher? (works at the same campus)
     ├── home_room?    (at the same campus)
     └── TeachingAssignment: Subject (must be in the curriculum) + teacher

Students module
  Enrollment.section: the student's placement
```

### Rules and where they're enforced

| Rule | Serializer (400) | Database |
|---|:-:|:-:|
| Codes unique per organization (departments, programs, subjects, batches) | ✓ | partial unique |
| Room code unique per campus | ✓ | partial unique |
| `last_level ≥ first_level` | ✓ | check |
| Range can't shrink past existing sections or curriculum | ✓ | |
| Curriculum level inside the program's range; no duplicates | ✓ | unique |
| Academic years don't overlap; end after start | ✓ | check (order) |
| One current academic year per organization | set-current action | partial unique |
| Terms inside their year, no overlap, unique sequence | ✓ | check, partial unique |
| Section unique by year + campus + program + level + name | ✓ | partial unique |
| Section's level in range; batch, room and class teacher match its campus | ✓ | |
| Section's year, campus, program and level locked once students are placed | ✓ | |
| Teaching assignment: subject in the curriculum for that level; teacher hasn't left | ✓ | unique triple |
| Deleting something still in use is refused (409 `in_use`) | view | PROTECT |

---

## Student placement

`Enrollment` (students module) gained one field, `section`. The section already
carries the year, program, level and batch, so nothing is copied.

`POST /students/{id}/place/ {"section", "on_date"?, "reason"?}`:

- **First placement** of an enrollment fills in its section.
- **Any later move** (section change, promotion to the next grade, a new
  year) closes the current enrollment as `moved` and opens a new one.
  The history lists every class the student has been in:

```
GET /students/{id}/enrollments/
  Grade 6 A   2083/84   active
  Grade 5 B   2082/83   moved     "Balancing class sizes"
  Grade 5 A   2082/83   moved
```

Refused: a section at another campus (transfer first), a section of an
academic year that has already ended (placing ahead into next year is
allowed), a graduated or withdrawn student, and the section they're already in.

A **transfer** to another campus opens an unplaced enrollment there. The
student is then placed in one of that campus's sections.

---

## Permissions

| Permission | campus-admin | staff |
|---|:-:|:-:|
| `academics.view` | ✓ | ✓ |
| `academics.manage_structure` (departments, programs, subjects, curriculum, years, terms) | | |
| `academics.manage_classes` (rooms, batches, sections, teaching assignments) | ✓ (own campus) | |
| `students.place` | ✓ (own campus) | |

`org-admin` holds everything. The organization-wide structure is deliberately
left to `org-admin` or a custom "Academic Office" role, because one campus
changing the shared curriculum would affect every campus.

Campus scoping: rooms, batches and sections by `campus`; teaching assignments
by `section.campus`.

---

## API

```
/api/v1/departments/            CRUD
/api/v1/programs/               CRUD
/api/v1/subjects/               CRUD
/api/v1/curriculum/             CRUD   ?program=&level= gives one grade's or semester's subjects
/api/v1/academic-years/         CRUD   POST {id}/set-current/
/api/v1/terms/                  CRUD   ?academic_year=
/api/v1/rooms/                  CRUD   campus-scoped
/api/v1/batches/                CRUD   campus-scoped
/api/v1/sections/               CRUD   campus-scoped; GET {id}/students/; student_count
/api/v1/teaching-assignments/   CRUD   campus-scoped; ?section= ?teacher= ?subject=
/api/v1/students/{id}/place/    POST
/api/v1/sections/{id}/promote/  POST   whole class
/api/v1/student-electives/      GET POST DELETE (drop = end from today)
/api/v1/calendar/               CRUD   ?from=&to= ?kind= ?campus= ?program= ?level=
```

---

## Tests

51 new tests (354 in the suite), plus 66 live checks on MariaDB in
`api-test-report.txt`. They cover every validation rule above, database
backstops, in-use deletes, placement history, campus scoping, tenant
isolation and permissions. List endpoints run a constant number of queries
regardless of row count.

---

## Placement over time

Enrollments cover `started_on` up to, but not including, `ended_on`, and
**dates decide who is in a class**, not status (`Enrollment.objects.on(date)`,
`students_in_section(section, on=date)`):

- A move with a future `on_date` is **scheduled**. The student stays in
  their class until then, and a whole class can be promoted ahead. Placing
  again revises the scheduled move, and placing back cancels it. Transfer
  and withdrawal are refused (409 `scheduled_move`) until it's cancelled.
- A graduation recorded ahead keeps the student in class until its date.
- **Capacity.** Placing or promoting into a full section is refused (409
  `over_capacity`) unless `allow_over_capacity` is sent.

## Electives per student

`StudentElective` records which elective a student takes. It belongs to the
student's **enrollment** (one stay in one section), so each class keeps its
own choices in the student's history. Compulsory subjects aren't recorded,
because every student of the section takes them.

```
POST   /api/v1/student-electives/  {"student", "subject"}   uses the student's current class
GET    /api/v1/student-electives/?section=&student=&subject=&current=true
DELETE /api/v1/student-electives/{id}/                       current class only; earlier ones are history (409)
GET    /api/v1/sections/{id}/students/?subject=<id>          who takes a subject
```

Rules:

- The subject must be an elective at the student's level and program.
- The student must be placed in a class.
- No duplicates.
- A student can't take two electives whose lessons meet at the same time.
- A move within the same level (11 A → 11 B) keeps the choices. A
  promotion (Grade 11 → 12) starts afresh, because the next level has its
  own curriculum.
- Choices are **dated** (`started_on`, `ended_on`). Dropping a subject ends
  the choice, so past dates still show it. A choice made today is simply
  removed. `in_section` picks next year's class for choosing ahead.
- The curriculum refuses to remove an elective students take, or to make it
  compulsory while it runs in parallel or has current choices.
- Writes need `students.place`. Reads need `academics.view` and
  `students.view`. Campus-scoped by the enrollment's campus.

## Whole-class promotion

`POST /api/v1/sections/{id}/promote/ {"to_section", "exclude"?, "on_date"?, "reason"?}`
moves everyone in a section to another section at the same campus. Use it
for end-of-year promotion, or to merge two sections. `exclude` lists
students who stay behind.

Each student goes through the same `place_student` rules. The move is all
or nothing: if any student can't be moved, nobody is. The response is
409 `promotion_failed`, with `details.students` listing each student who
failed and why. Needs `students.place`.

## Teaching assignments

- `role`: `lecture` (default), `practical`, `tutorial` or `co_teaching`. One
  subject can have several teachers; a teacher can hold several roles.
- `periods_per_week` (optional, 1–60): how many lessons a week. The timetable
  generator fills up to this number ([`phase-3b.md`](phase-3b.md)).
- `is_active`: false once handed over. Kept as a record, with no new
  lessons. An assignment whose lessons have run can't be deleted, only
  retired.

## Academic calendar

`/api/v1/calendar/`: holidays, closures, exams, events and make-up days, for
every campus or one, optionally one program and grade. See
[`phase-3b.md`](phase-3b.md#the-academic-calendar) for how the timetable
uses it.

## Staff leaving

Marking a staff member `left` is refused while they have lessons, planned
cover, or a class-teacher role from the leaving date on. Hand the work over
first.

All real-life situations and their tests: [`phase-3-real-life.md`](phase-3-real-life.md).
