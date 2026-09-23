# Phase 3a — Academic Structure

What is taught, when, where and to which groups, and which group each
student is in. Phase 3b (next) adds the timetable on top of this.

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
```

---

## Tests

51 new tests (354 in the suite), plus 66 live checks on MariaDB in
`api-test-report.txt`. They cover every validation rule above, database
backstops, in-use deletes, placement history, campus scoping, tenant
isolation and permissions. List endpoints run a constant number of queries
regardless of row count.

---

## Not in 3a

- **Timetable** (periods, weekly schedule, clash detection for teacher, room
  and section) is Phase 3b, next.
- **Bulk promotion** (move a whole section to next year's section in one call)
  is a natural follow-up. Today placement is per student.
- **Electives per student.** Curriculum marks subjects as elective, but which
  student takes which elective isn't recorded yet. Exams (Phase 5) will need it.
