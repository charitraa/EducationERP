# Phase 3 — Real-life checks

Real situations in schools, +2 colleges and universities, what the system
does in each one, and the test that proves it. Everything marked ✅ is
built and tested, including the attendance situations Phase 3 prepared for
and Phase 4 built.

The rule behind most of this: **don't depend on current state alone.**
Enrollments, elective choices, lessons, bell times and teaching assignments
all carry dates. Whatever happened on a past date can still be read
correctly after students move, teachers leave or the timetable changes.

Test files are under `backend/modules/`.

---

## Students and classes

| Situation | What happens | Test |
|---|---|---|
| ✅ **Promotions entered ahead.** In Chaitra the office moves Grade 11 A to Grade 12 A, effective next Baisakh. | The move is scheduled. Students stay in Grade 11 A until the date, and class lists and counts don't change early. | `students/tests/test_scheduled_moves.py` |
| ✅ The office changes its mind before the date. | Placing again revises the scheduled move. Placing back into the current class cancels it. | same |
| ✅ A student with a scheduled move is withdrawn or transferred. | Refused with 409 `scheduled_move`. Cancel the move first, so the history can't contradict itself. | same |
| ✅ **A student joins mid-year** (admitted 15 September). | They're in the class from their start date, not before, so attendance won't expect them on 1–14 September. | same |
| ✅ **A student has several enrollments**: repeating, a new year, a transfer, re-admission. | Every stay in a class is its own enrollment with dates. "Which class on date D" is always answerable. | same, `students/tests/` |
| ✅ **A class is full** (capacity 40). | Placing or promoting the 41st student is refused with 409 `over_capacity`, unless `allow_over_capacity` is sent. | same |
| ✅ **A student drops an elective** mid-year. | The choice is ended, not deleted. Past dates still show they took it, and new dates don't. They can take it up again later. | same |
| ✅ A student chooses electives for next year's class in advance. | `in_section` picks the class they're promoted into. | `academics/tests/test_electives_promotion.py` |
| ✅ A student chooses two electives that are taught at the same time. | Refused. | same |
| ✅ **The same subject in several sections** (Database for BCA A, B and C). | Each section has its own teaching assignment. A subject is never "one class". | `academics/tests/` |

## Teachers

| Situation | What happens | Test |
|---|---|---|
| ✅ **A teacher leaves the college.** | Marking them "left" is refused while they still have lessons, planned cover, or a class-teacher role from the leaving date on. Hand the work over first. | `timetable/tests/test_guards.py` |
| ✅ **Hand-over** (Mathematics moves from Teacher A to Teacher B). | Takes effect from a date. Lessons that already ran keep Teacher A, and Teacher B takes them from that date. The old assignment is kept, marked inactive, and can't get new lessons. | `timetable/tests/test_combined_handover.py` |
| ✅ **A teacher is absent today**: Shyam covers Ram's class. | A lesson change for that date. Ram's assignment isn't touched. Shyam's day view includes the class, and Ram's leaves it out. | `timetable/tests/test_lesson_changes.py` |
| ✅ The substitute is already teaching then, or is on leave. | Refused: 409 clash, or 400 "on leave". A teacher freed by a cancellation, or covered by someone else, counts as free. | same, `test_guards.py` |
| ✅ **Two teachers for one subject**: theory by A, lab by B. | Teaching assignments have a `role`: lecture, practical, tutorial or co-teaching. | `timetable/tests/test_capacity_roles.py` |
| ✅ Lab groups in parallel (half the class with each teacher). | Two practical assignments of one subject may meet at the same time, in different rooms. A lecture alongside them is still refused. | same |
| ✅ Co-teaching (two teachers in one room). | A co-teaching assignment may share the time and room. | same |
| ✅ **A teacher is double-booked** (10:00 BCA A and 10:00 BCA B). | Refused with 409 `timetable_clash`, at any campus. | `timetable/tests/test_entries.py` |

## Timetable

| Situation | What happens | Test |
|---|---|---|
| ✅ **The timetable changes mid-semester** (Monday 10:00 Database moves from Room 201 to Room 305 in October). | An edit applies from `effective_from`, default today. A lesson that already ran is ended and continued as a new version, so September still reads Room 201. | `timetable/tests/test_versioning.py` |
| ✅ A lesson is removed mid-year. | It ends the day before. Its past stays, and its future substitutions go with it. | same |
| ✅ A lesson is added mid-year. | It runs from today, not retroactively. | same |
| ✅ **Winter timings** (school starts 30 minutes later from Poush). | `POST /bell-schedules/{id}/retime/` moves the periods and their lessons from a date. The past keeps the old times. If a moved lesson would clash with another shift, nothing moves. | `timetable/tests/test_retime.py` |
| ✅ **A room is double-booked.** | Refused with a 409 clash. | `test_entries.py` |
| ✅ **A room is too small**: two sections of 45 combined into a 60-seat hall. | Refused with 409 `room_too_small`, unless `allow_over_capacity` is sent. Electives count only the students who take them. | `test_capacity_roles.py` |
| ✅ A room is closed for repair. | Refused while lessons or planned changes still use it. Move them first (a room change can start from a date). | `test_guards.py` |
| ✅ A combined class gets a substitute. | The change applies to every section of the class, so it's never one hall with two teachers. | `test_combined_handover.py` |
| ✅ A combined class moves to another time. | Every section moves together, from a date. | `test_versioning.py`, `test_combined_handover.py` |

## Calendar

| Situation | What happens | Test |
|---|---|---|
| ✅ **Public holidays** (Dashain, Tihar). | Calendar event of kind `holiday`. Lessons that day show as cancelled with the reason, and substitutions that day are refused. | `timetable/tests/test_calendar.py` |
| ✅ **Closures** (strike, snow day) at one campus. | Kind `closure`, limited to that campus. | same |
| ✅ **Exam days replace the normal timetable**, for one grade only. | Kind `exam`, limited to a program and level. Other grades keep their lessons. | same |
| ✅ A college event where classes still run. | Kind `event`, which doesn't suspend classes unless told to. | same |
| ✅ **A make-up Saturday** that runs Sunday's timetable. | Kind `makeup_day` with `runs_timetable_of: 7`. | same |
| ✅ A campus admin adds a closure for their own campus. | Allowed, and they see holidays shared by every campus. Adding events for every campus needs an organization-wide role. | same |

## Curriculum

| Situation | What happens | Test |
|---|---|---|
| ✅ An elective is removed while students still take it. | Refused with 409. | `test_guards.py` |
| ✅ An elective becomes compulsory while it runs in parallel with another elective. | Refused: that would be two compulsory lessons at once. It's also refused while current students have chosen it. | same |

---

## ✅ Phase 4 (attendance): built

Phase 3 recorded what attendance would need. Phase 4 built every item. Details:
[`phase-4.md`](phase-4.md). Tests are in `backend/modules/attendance/tests/`.

| Situation | What Phase 4 does | Test |
|---|---|---|
| ✅ History stays with the enrollment that existed when attendance was taken. | Each record stores its `enrollment`. After a move, the record still names the old class. | `test_marking.py` (HistoryTests) |
| ✅ A teacher leaves; B replaces A. | `marked_by` is who actually marked, and the session keeps the day's teacher. Neither is recomputed. | same |
| ✅ A substitute marks attendance. | The session's teacher is the teacher for that day from `lessons_on`, after substitutions. Only they (or the office) can take it. | `test_sessions.py` |
| ✅ Class sessions ("Database, BCA A, 10:00, Monday"). | Built from the day's lessons. Cancelled lessons, holidays and closures are refused with the reason. `sessions/mine/` lists a teacher's classes for the day. | `test_sessions.py` |
| ✅ Marking attendance twice. | UNIQUE(session, enrollment) in the database, plus upsert. Opening a session twice returns the same one. | `test_marking.py`, `test_sessions.py` |
| ✅ Corrections after submitting. | Reason required. `AttendanceCorrection` keeps old status, new status, who and when, and the audit log gets an entry. | `test_marking.py` (CorrectionTests) |
| ✅ A student marks another student present (QR). | The student comes from the login: user → student → enrollment on that date. A `student` or `enrollment` in the request is ignored. | `test_qr.py` |
| ✅ A QR screenshot gets shared. | Signed codes expire in 15–300 s. Optional location radius, and one phone can't mark two students. | `test_qr.py` |
| ✅ The same QR is scanned twice. | `200 already_marked`. | `test_qr.py` |
| ✅ Offline marking, then sync. | A `client_key` per record makes a resent sync a no-op. `recorded_at` is the client's time; `created_at` / `updated_at` is when the server got it. | `test_marking.py` |
| ✅ Only students taking the subject are expected. | Expected = `students_taking(section, subject, on=date)`, among the class's enrollments that day. | `test_marking.py`, `test_qr.py` |
| ✅ Statuses beyond present/absent. | present, absent, late, excused, leave, medical leave, on duty. Excused kinds are left out of percentages. | `test_marking.py`, `test_reports.py` |
| ✅ QR and biometric input. | Every input — teacher, office, QR, ZKTeco, the generic device API — goes through `modules/attendance/services.py`. | `test_qr.py`, `test_devices.py` |

---

## Not modelled (by choice, for now)

- **Curriculum isn't per academic year.** Making an elective compulsory
  affects the program level for every year, which is why it's refused
  while current students have chosen it.
- **Teacher availability** (part-time hours, maximum periods a day) and
  travel time between campuses. The generator doesn't know about them.
- **Labs for practicals, and double periods** in the generator. Place
  those lessons by hand.
- **Seat limits per elective.**
