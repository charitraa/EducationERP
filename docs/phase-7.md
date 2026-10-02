# Phase 7 — Events and student points

Events, registration, attendance and participation at them, and the points,
achievements, badges and titles they can earn. Everything follows the
conventions in [`phase-1.md`](phase-1.md).

```
EventCategory
  └─► Event (campus, or every campus)
        ├─► EventRegistration (open | approval, capacity)
        ├─► EventAttendance   (its own check-in — never Phase 4's tables)
        └─► EventParticipation (role, and a competition's placing)
                    │
           PointRule (configurable) ──► PointEntry ──► StudentPoints (running total)
                                                              │
                                                        AwardRule (configurable)
                                                              ▼
                                                    Award ──► StudentAward
                                             (achievement | badge | title)
```

Code: `backend/modules/events/`. `services.py` runs the registration and
event-day workflow and awards points; `selectors.py` renders rosters,
summaries and the leaderboard as data.

---

## Events never touch Attendance's tables

claude.md is explicit that cross-module writes like "Events directly
modifying Attendance tables" are the wrong pattern. `EventAttendance` is its
own simple present/absent check-in — a different model, a different table,
nothing in common with Phase 4's `AttendanceSession`/`AttendanceRecord`
beyond the idea of "who showed up." A student's academic attendance and
their sports-day attendance are unrelated facts, and the code keeps them
that way.

## One event, at one campus or every campus

An **Event** belongs to an **EventCategory** (sports, cultural, academic
club, …) and, optionally, one campus — empty means every campus, shared the
same way Phase 3a's calendar events are: a campus-scoped role sees and can
register for a shared event, but only an organization-wide role can create
one (`EventViewSet._check_scope`, the same rule `CalendarEventViewSet`
already applies).

It moves `draft → published → cancelled`. Only a draft can be deleted
(409 `not_draft` otherwise): a published event's registrations,
attendance and the points it earned stay with it, so it is cancelled
instead, as an exam is. Registration only opens once published, and
follows one of three modes set per event:

| Mode | What happens |
|---|---|
| `none` | No sign-up; attendance is taken for whoever shows up |
| `open` | Registering confirms it immediately |
| `approval` | Registering leaves it `pending`; the organizer or the office decides |

Students sign up themselves (`events/{id}/register/`). The organizer or the
office can also enter a student (`POST /event-registrations/`, for a team
sheet or a student without an account): the same rules — open, not full,
not twice, the event's own campus — and, since they could approve it
anyway, it's confirmed at once.

An optional **capacity** closes registration once that many are
**confirmed** — never a waitlist, and never counting a `pending` request
until it's approved. Withdrawing frees the slot.

## Who runs an event

Publishing, cancelling and the fee-structure-style setup (categories, point
rules, award rules) are the office's job (`events.manage`). Day-to-day
running one — entering and deciding registrations, taking attendance,
recording participation — belongs to whoever is named as the event's `organized_by`,
the same shape as a subject teacher marking their own class's exam (Phase
5): checked against the event, not just a permission code
(`services.ensure_can_run`). The office can still step in on any event.

## Attendance and participation are different questions

**EventAttendance** answers "were they there": present or absent, one row
per student per event, idempotent to mark again (re-marking present doesn't
re-award anything). **EventParticipation** answers "what did they do":
a role (participant, winner, runner-up, organizer, volunteer) and, for a
competition, where they placed — a student can hold several roles at once,
but recording the same role again just updates it (a corrected placing),
never duplicates it.

Both only for a published event whose day has come, in the organization's
timezone (409 `not_published` for a draft or cancelled one, `not_started`
for a future one): they award points, so nobody can be checked in to, or
win, an event that hasn't happened. The day, not the minute, so the gate
can check people in before it starts.

## Points: a general ledger, configured by rule

A **PointRule** says how many points a way of taking part is worth — for
attending a category (or every category, left blank), or for a
participation role. The moment a student first becomes present at an event,
or a role is first recorded for them, any matching rules fire and post a
**PointEntry**. `StudentPoints` keeps the running total in step inside the
same transaction — worked out once, the same way `Invoice.paid_amount` and
`StaffAttendanceDay` are, never by a background job. The office can also
award (or deduct) points by hand, with a reason, for anything a rule
doesn't cover.

The ledger doesn't assume events are its only source: a later module could
post its own `PointEntry` rows without any change here.

## Achievements, badges and titles: one model, one rule engine

Structurally an achievement, a badge and a title are the same thing — a
named recognition a student holds — so they're one **Award** model with a
`kind`. An **AwardRule** grants one automatically once a student crosses a
threshold: a points total, or a count of events attended or won (optionally
scoped to one category). The check runs right when the points or the count
behind it changes (inside `award_points`, `mark_attendance` and
`record_participation`) — never a cron job. The office can also grant one by
hand. Ending one (`POST /student-awards/{id}/end/`) keeps the record rather
than deleting it, mainly meant for a title that moves to someone else.

## API surface

```text
GET    /api/v1/event-categories/                                     CRUD
GET    /api/v1/events/                                               CRUD (org-wide event needs an org-wide role)
POST   /api/v1/events/{id}/publish/  cancel/
POST   /api/v1/events/{id}/register/                                  students; confirmed or pending by mode
GET    /api/v1/events/{id}/registrations/  attendance/  participation/  roster/
POST   /api/v1/events/{id}/mark-attendance/  record-participation/    the organizer, or the office
GET    /api/v1/events/me/                                             a student's/parent's own events
GET    /api/v1/event-registrations/                                   list/retrieve
POST   /api/v1/event-registrations/                                   organizer or office enters a student
POST   /api/v1/event-registrations/{id}/decide/  withdraw/
GET    /api/v1/point-rules/                                           CRUD
GET    /api/v1/point-entries/                                         list/retrieve; POST awards by hand
GET    /api/v1/student-points/  student-points/leaderboard/  student-points/me/
GET    /api/v1/awards/  award-rules/                                  CRUD
GET    /api/v1/student-awards/                                       list/retrieve; POST grants by hand
POST   /api/v1/student-awards/{id}/end/
```

## Not built (by choice, for now)

- **Waitlists.** A full event refuses further registration outright; nobody
  is queued for a freed slot.
- **A public event calendar / feed.** `events/me/` is per-student; a
  campus-wide "what's on" view for anyone to browse is a communication
  concern (Phase 8).
- **Notifications** ("your registration was approved", "you earned a
  badge"). Needs the Phase 8 notification service.
- **Points/awards fed by other modules** (an attendance streak, a library
  return, a payment made on time). The ledger and rule engine don't assume
  events are the only source, but nothing outside this module posts to them
  yet.
