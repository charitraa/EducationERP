# Phase 4 — Attendance

Student attendance (a daily roll call for schools, every lesson for
colleges), staff attendance, and one engine behind every way of taking it:
the teacher's app, the office, QR codes, biometric devices and the API.
Everything follows the conventions in [`phase-1.md`](phase-1.md).

```
Teacher app ─┐                 ZKTeco (push) ─┐
Office ──────┤                 Generic API  ──┤  integrations/biometric/
QR scan ─────┤                                ▼
             │                            DevicePunch
             ▼                                │
      modules/attendance/services.py ◄────────┘
             │
   ┌─────────┴──────────────┐
   ▼                        ▼
AttendanceSession        Punch ──► StaffAttendanceDay
   └─► AttendanceRecord ─► AttendanceCorrection
```

Code: `backend/modules/attendance/` (the engine) and
`backend/integrations/biometric/` (device adapters). The core never knows
which brand of device sent a punch.

---

## Two ways of taking student attendance

`Program.attendance_mode` chooses, per program:

| Mode | Who takes it | When | Typical for |
|---|---|---|---|
| `daily` | The section's class teacher | Once a day | Schools, Grade 1–10 |
| `lesson` | The lesson's teacher **that day** (a substitute, if there is one) | Every lesson on the timetable | +2 and university programs |

Opening the wrong kind for a program is refused with `400 wrong_mode`.

## Data model

### AttendanceSession
One roll call: a section's day (`kind=daily`), or one lesson on one date
(`kind=lesson`, with its `timetable_entry`). It stores `teacher`, meaning who
takes it that day, worked out when it's opened. That's the substitute when a
lesson change names one, never "today's teacher" worked out later.

- Unique: one daily session per (section, date); one lesson session per
  (timetable entry, date). Opening again returns the same session (`200`),
  so a double tap or a retry never makes two.
- `status`: `open` → `submitted`. Only the office can reopen.

### AttendanceRecord
One student in one session. It points at the **enrollment** on that date,
not just the student, so the record stays with the class the student was in
after they move, get promoted or change sections.

- Unique `(session, enrollment)`: a student can't be marked twice.
- `status`: present, absent, late, excused, leave, medical leave, on duty.
- `source`: teacher, manual (office), qr, biometric, api.
- `marked_by`: who actually marked it. Never derived later.
- `recorded_at` and `client_key` support offline marking (below).

### AttendanceCorrection
Written when a record changes **after** its session was submitted: old
status, new status, reason (required), who and when. Append-only. An audit
log entry is written too.

### Staff: WorkSchedule, StaffWorkSchedule, Punch, StaffAttendanceDay
- **WorkSchedule** (per campus): start and end time, `grace_minutes` (later
  than that is late), `half_day_minutes` (working less than that, with a
  check-out, is a half day), and working `weekdays`. One default per campus.
  **StaffWorkSchedule** gives one person their own schedule.
- **Punch**: one raw event (a finger on a reader, a gate QR scan, an office
  entry). Append-only, never edited. `dedupe_key` is unique per
  organization, so a device resending the same event is ignored.
- **StaffAttendanceDay**: one row per person per day, worked out from their
  punches: first punch in, last punch out, minutes worked, and status
  (present, late or half day). The office can set a day by hand (leave, on
  duty, a forgotten punch). Punches then leave it alone until the override
  is cleared.

### Devices: AttendanceDevice, BiometricIdentity
- **AttendanceDevice**: a reader at a campus, of kind `zkteco` or
  `generic`. Serial numbers are unique across **all** organizations,
  because a ZKTeco device identifies itself only by its serial.
- **BiometricIdentity**: which staff member or student a device user number
  (PIN) belongs to, unique per organization. A punch from an unmapped PIN is
  kept. When the PIN is mapped later, its punches are applied.

None of these models are soft-deleted: attendance is a record of what
happened.

---

## Rules

| Situation | What happens | Test |
|---|---|---|
| A teacher opens a session for a class that isn't theirs | `403 not_your_class`, nothing is created | `test_sessions.py` |
| A substitute covers the lesson | The substitute takes it; the regular teacher can't | `test_sessions.py` |
| Future date | `400 future_date`. "Today" is the **organization's** today, in its time zone | `test_sessions.py` |
| Cancelled lesson, holiday, closure | `409 lesson_cancelled` / `409 no_classes`, with the reason | `test_sessions.py` |
| Exam day for another grade | Doesn't stop this class | `test_sessions.py` |
| Daily roll call on a weekend | Refused if the class has a timetable and no lessons that day. A school that keeps no timetable can take it any day it's open | `test_sessions.py` |
| Who is expected | Everyone in the class that day; for an elective lesson, only those who chose it. Students who joined later, or left, aren't expected | `test_marking.py` |
| A student from another class | `400 not_expected` | `test_marking.py` |
| "All present except …" | `mark` with `records` plus `rest: present` | `test_marking.py` |
| Marking twice (double click, retry) | The unique constraint and upsert: one record | `test_marking.py` |
| Offline marking, synced later | `client_key` per record: a resent sync is skipped; `recorded_at` keeps when it was taken | `test_marking.py` |
| Submit with students unmarked | `409 unmarked` lists them, unless `rest` says what they are | `test_marking.py` |
| Change after submitting | Needs a reason. Kept as a correction plus an audit entry | `test_marking.py` |
| A student later moves class | The record still points at the old enrollment and section | `test_marking.py` |
| Teacher A hands the class over to B | Records keep `marked_by` A, and the session keeps teacher A | `test_marking.py` |

### QR

The teacher asks for a code (`POST sessions/{id}/qr/`) and shows it. Codes
are signed, can't be edited, and expire in 15–300 seconds (default 60), so
the screen asks for a new one each minute.

| Situation | What happens |
|---|---|
| Student scans | Their login decides who they are: user → student → enrollment that day. A `student` or `enrollment` sent in the request is ignored |
| Scans twice | `200 already_marked` |
| A screenshot shared to a group chat | Expires within a minute (`403 qr_expired`). With `latitude/longitude/radius` on the code, scans from further away are refused (`403 too_far`) |
| One phone used for two students | `403 device_used` (the app sends `device_id`) |
| The teacher already marked them | The teacher's mark stays |
| After `late_after` | Marked late |
| Parent or teacher account scans | `403 not_a_student` |
| Session submitted | `409 session_submitted` |

Staff check in the same way at the gate: the office shows a campus code
(`POST punches/qr/`) and staff scan it (`POST punches/check-in/`).

### Student punches at the gate

A student's biometric punch marks them present in their class's **daily**
roll call. That only happens if the roll call isn't submitted and nobody has
marked them yet: a teacher's mark always wins. Lesson attendance is never
taken from the gate, because being at the gate isn't being in Physics.

### Percentages

Present, late and on duty count as **attended**. Excused, leave and medical
leave are **left out altogether**, so sick leave doesn't count against a
student:

```
percentage = attended / (sessions − excused)
```

### Staff absences

Absent = days set to absent by hand, plus working days with no record at
all. Working days follow the person's schedule (Sunday–Friday when they have
none). Days before they joined, after they left, whole-campus holidays and
closures, and future days don't count.

---

## API

```
POST   /api/v1/attendance/sessions/                    open {timetable_entry | section, date?}; 200 if already open
GET    /api/v1/attendance/sessions/                    list (?section= &date= &date_from= &date_to= &status= &teacher=)
GET    /api/v1/attendance/sessions/mine/?date=         a teacher's classes to take that day, with session status
GET    /api/v1/attendance/sessions/{id}/roster/        who is expected, and each one's mark so far
POST   /api/v1/attendance/sessions/{id}/mark/          {records: [{enrollment, status, note?, client_key?, recorded_at?}], rest?}
POST   /api/v1/attendance/sessions/{id}/submit/        {rest?}
POST   /api/v1/attendance/sessions/{id}/reopen/        office only
POST   /api/v1/attendance/sessions/{id}/qr/            {ttl?, late_after?, latitude?, longitude?, radius?} -> token
POST   /api/v1/attendance/sessions/scan/               students: {token, latitude?, longitude?, device_id?}

GET    /api/v1/attendance/records/                     list (?session= &student= &section= &status= &date_from= &date_to=)
PATCH  /api/v1/attendance/records/{id}/                {status, reason, note?}: a correction once submitted
GET    /api/v1/attendance/records/me/                  students: own; parents: a child's (?student=)

GET    /api/v1/attendance/reports/student/?student=    overall, daily and per subject
GET    /api/v1/attendance/reports/register/?section=   students × sessions grid with totals
GET    /api/v1/attendance/reports/defaulters/          ?section= | ?program=, &below=75
GET    /api/v1/attendance/reports/missing/?date=       lessons and roll calls not submitted
GET    /api/v1/attendance/reports/staff/               ?campus= &staff=: present, late, half day, absent, average hours
       (reports take ?from= &to=, default the last 30 days, at most 400 days)

GET    /api/v1/attendance/staff-days/                  list; POST sets a day by hand {staff, date, status, note}
DELETE /api/v1/attendance/staff-days/{id}/             clear a hand-set day, back to the punches
GET    /api/v1/attendance/staff-days/me/               staff: own
GET    /api/v1/attendance/punches/                     list; POST a manual punch {staff, punched_at, note}
POST   /api/v1/attendance/punches/qr/                  office: a gate code {campus, ttl?, location?}
POST   /api/v1/attendance/punches/check-in/            staff: {token, latitude?, longitude?}
GET    /api/v1/attendance/work-schedules/  staff-schedules/          CRUD
GET    /api/v1/attendance/devices/                     CRUD; a generic device's API key is shown once
POST   /api/v1/attendance/devices/{id}/rotate-key/
GET    /api/v1/attendance/biometric-ids/               CRUD: PIN -> staff or student

POST   /api/v1/attendance/device-punches/              devices: Authorization: Device <key>
GET|POST /iclock/cdata  /iclock/getrequest  /iclock/devicecmd   ZKTeco push protocol
```

### Permissions

| Code | Meaning | Shipped to |
|---|---|---|
| `attendance.mark` | Take attendance for **your own** classes. The service checks you're the day's teacher or the class teacher | `staff` |
| `attendance.view` | Sessions, records and reports at your campuses | `campus-admin` |
| `attendance.manage` | Any class at your campuses, reopen, staff days, manual punches, gate codes | `campus-admin` |
| `attendance.devices` | Devices and PIN mapping | `campus-admin` |

The `me` endpoints and `scan` / `check-in` need only a login; the profile
linked to the account decides what they return.

---

## Connecting devices

**Set the organization's time zone first** (`PATCH /organizations/{id}/`
with `"timezone": "Asia/Kathmandu"`). Devices send local times without a
zone, and "today" is decided in that zone.

### ZKTeco (push / ADMS)
1. `POST /attendance/devices/` with `kind: zkteco` and the serial number
   printed on the device. Set `allowed_ips` to the school's public IP
   address.
2. On the device: *Comm → Cloud Server Setting*. Set the server address to
   this server, port 443 (HTTPS), and turn on *Enable domain name* if you
   use one.
3. Map each device user number to a person:
   `POST /attendance/biometric-ids/ {pin, staff | student}`.

The protocol has no password. A device is known only by its serial number,
which is printed on the device. **Always set `allowed_ips`**, or anyone who
learns the serial could send punches.

### Anything else (generic API)
`POST /attendance/devices/` with `kind: generic` returns an `api_key` once.
The device or its vendor software then sends:

```http
POST /api/v1/attendance/device-punches/
Authorization: Device <api_key>

{"punches": [{"pin": "1001", "time": "2026-09-24T09:58:12", "direction": "in"}]}
```

Resending is safe: duplicates are counted and skipped. The response lists
PINs that aren't mapped yet. Rate limit: `THROTTLE_DEVICE` per device.

---

## Not built (by choice, for now)

- **Leave requests and approval** (students and staff). A day or record can
  be set to leave by hand. The request workflow belongs with HR (Phase 11)
  and communication (Phase 8).
- **Notifications** ("your child was absent"). These need the notification
  service (Phase 8). `AttendanceMarked` is the event to publish.
- **Face recognition.** It would be another adapter under
  `integrations/biometric/`, sending punches like the others.
- **Other device brands' own protocols** (Hikvision, eSSL, …). They can use
  the generic API through their vendor software today, or get an adapter.
- **Sending commands to ZKTeco devices** (uploading users, clearing logs).
  The server only receives punches.
- **Shift work across midnight.** A staff day is one calendar day in the
  organization's time zone.
- **A background job that writes absent rows.** Absences are worked out when
  a report is read, so there's nothing to schedule.
