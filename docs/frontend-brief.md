# Frontend brief — Education ERP

This document is for whoever designs and builds the user interface, a
person or an AI. It describes the finished backend: who uses it, what
each person sees, how the API behaves, and what the UI must get right.

**What to hand over**

| File | What it is | How to use it |
|---|---|---|
| `docs/frontend-brief.md` | This brief | Paste it whole into the chat |
| `docs/api/endpoints.md` | Every endpoint (933), one line each | Paste it too, or the sections for the screen being built |
| `docs/api/openapi.yaml` | The full contract: every field, type, enum and error | Too big to paste. Give it to the coding tool, or generate a typed client from it (e.g. `openapi-typescript`, `orval`) |

`claude.md` holds backend coding rules; the UI doesn't need it.

After a backend change, regenerate the two API files:
`cd backend && python manage.py spectacular --file ../docs/api/openapi.yaml && python ../docs/api/make_index.py`

---

## 1. What the product is

A school and college management system (ERP) for Nepal and similar
markets, offered as a free online service. Each **organization** (a
school, college or university) sees only its own data. An organization
has one or more **campuses** (branches). Most schools have one, and then
the UI must never mention campuses (see §4).

Modules, all built and tested: students and admissions, parents, staff,
academics (programs, subjects, classes, academic years and terms),
timetable, attendance (classes, staff, QR, biometric devices),
examinations (marks, grades, results, report cards, transcripts),
finance (fees, invoices, payments, scholarships), events and student
points, notices, notifications, messaging and appointments, support
tickets, library, inventory, HR (contracts, leave), payroll, hostel,
transport, applications (one approval workflow for many request types,
with public admission forms), certificates, alumni, careers (own hiring
and a job board), and file uploads.

Dates are AD (ISO `YYYY-MM-DD`). Bikram Sambat names, such as an academic
year called `2082/83`, are free text the school types in. Money is a
decimal **string** (`"1500.00"`); never use floats for it.

## 2. Who uses it

`user_type` says what a person is. **Access comes from roles and
permissions, never from `user_type`.**

| user_type | Who | Typical screens |
|---|---|---|
| `administrator` | Owner, principal, office | Everything their roles allow; setup |
| `staff`, `teacher` | Employees | Their timetable, roll call, marks for their classes, leave, payslips, plus office screens their roles allow |
| `student` | Enrolled students | Own timetable, attendance, results, invoices, library, hostel, bus, events, applications |
| `parent` | Guardians | The same as their child, with a child switcher |
| `alumni` | Graduates (their login stays) | Alumni profile, directory, events, mentoring, donations, jobs |
| *(public)* | No account | Admission form, careers page, application status |

`integration` is an API key's identity (§7). It never appears in the UI;
leave it out of every user-type picker.

## 3. API basics

- **Base URL** `/api/v1/`. JSON in and out. File uploads are multipart.
- **Login** `POST /auth/login/ {email, password}` returns `{access, refresh,
  user}`. `user` is the same object as `GET /auth/me/`.
  - **Two-factor:** if the account has it, the first attempt fails with
    `400` and `error.code = "otp_required"`. Show a code field and send
    `{email, password, otp}` again. A recovery code works in `otp` too.
  - Wrong password: `400 invalid_credentials`. Too many attempts: `429`
    (the account is locked for a while; show "try again later").
- **Tokens.** Send `Authorization: Bearer <access>`. Access lasts 60
  minutes. When a call returns 401, call `POST /auth/refresh/ {refresh}`.
  It returns a new access token **and a new refresh token**; the old
  refresh token stops working, so store the new one. If the refresh fails,
  go to login. Logout is `POST /auth/logout/ {refresh}`.
- **`GET /auth/me/`** returns `{id, email, full_name, user_type,
  organization {id, name, code}, roles [...], permissions [...codes]}`.
  Build menus from `permissions` (§5). Still handle 403 everywhere: the
  server always decides.
- **Errors** always look like:
  ```json
  {"error": {"code": "machine_code", "message": "Human sentence.", "details": {"field": ["problem"]}}}
  ```
  - `400`: show `details` next to the matching form fields and `message`
    above the form.
  - `401`: refresh or log in again.
  - `403`: "you can't do this". Hide the action next time.
  - `404`: not found, which includes *another organization's or another
    campus's record*. Never retry it.
  - `405`: the action doesn't exist.
  - `409`: a business rule said no (`code` tells which, `message` says
    why in plain words: show it).
  - `429`: slow down.
- **Lists** are paginated: `{count, total_pages, page, page_size, next,
  previous, results}`. Use `?page=` and `?page_size=` (max 200).
  `?search=` searches text. `?ordering=field` or `-field` sorts. Filters
  are named fields, e.g. `?campus=3&status=active`. `endpoints.md` and
  the OpenAPI file list each list's filters.
- **Enums.** Every status and kind is a fixed set; `openapi.yaml` lists
  them with labels (e.g. `AdmissionStatusEnum`). Show the label, send the
  value.
- **Downloads** (résumés, files) need the auth header, so a plain `<a
  href>` won't work. Fetch the file as a blob and save it. Files always
  come as attachments.
- **Uploads**: `POST /files/` (multipart: `file`, `purpose`) returns
  `{id, ...}`. Then send that `id` where a record wants a file (e.g.
  `resume_file`). PDF, Word (.docx) and images only; 5 MB.

## 4. Make setup easy (most important)

Most schools are one campus, set up by one non-technical person. The UI
must hide everything they don't need.

- **One campus = no campus UI.** When the organization has one campus
  (`GET /campuses/`), fill `campus` in every form automatically and hide
  every campus column, filter and picker. Show them only once a second
  campus exists. "Add a branch" lives in Settings.
- **A setup wizard on first login**, each step skippable and resumable,
  with a checklist on the dashboard until done:
  1. School details (name, address, logo later) — `PATCH /organizations/{id}/`
  2. Academic year and terms (e.g. `2082/83`) — `/academic-years/`, `/terms/`
  3. Programs and levels (e.g. "+2 Science", grades 11–12), subjects,
     and which subjects each level takes — `/programs/`, `/subjects/`,
     `/curriculum/`
  4. Classes (sections) for this year — `/sections/`
  5. Staff, and give people logins and roles — `/staff/`, `/users/`,
     `/users/{id}/assign-role/`
  6. Students (admission, or straight in) and parents — `/admissions/`,
     `/students/`, `/parents/`
  7. Optional, from a "Turn on more" page: fees, exams and grading,
     timetable, library, hostel, transport, HR and payroll, alumni,
     careers
- **Ship templates** where the backend lets the school choose: a grade
  scale (A+ … NG with grade points), common fee items, a leave-type set,
  and common application forms (leave, certificate, hostel) with
  approval steps filled in. One click creates them through the normal
  endpoints.
- **Roles.** Shipped roles are `org-admin` (everything), `campus-admin`
  (a branch's office), `staff` (baseline employee), `student` and
  `parent`. A "who can do what" screen should create custom roles from
  checkboxes grouped by module (`GET /permissions/`), not raw codes.
- **Plain words.** Say "class" not "section", "fee" not "invoice item",
  "branch" not "campus". Show every server `message` as-is: they are
  written for school staff.

Signup: there is no self-signup yet. An organization is created on the
server today (`manage.py bootstrap_organization`). A public **sign-up
page** (school name, email, password, email verification) is the next
backend step; design the page now and wire it when the endpoint lands.

## 5. Menus by permission

Show a menu item when `me.permissions` contains its code. `org-admin`
has every code.

| Menu | Shown with | Main endpoints |
|---|---|---|
| Dashboard | everyone | (per role; see §6) |
| Students | `students.view` | `/students/`, `/students/{id}/change-status/`, `/students/{id}/place/` |
| Admissions | `admissions.view` | `/admissions/`, `{id}/approve|reject|enroll/` |
| Parents | `parents.view` | `/parents/`, `{id}/link-student/` |
| Staff | `staff.view` | `/staff/` |
| Academics | `academics.view` | `/programs/`, `/subjects/`, `/sections/`, `/academic-years/`, `/terms/`, `/rooms/`, `/calendar/` |
| Timetable | `timetable.view` | `/timetable/`, `/bell-schedules/`, `/lesson-changes/` |
| Attendance | `attendance.mark` / `attendance.view` | `/attendance/sessions/`, `/attendance/reports/...`, `/attendance/staff-days/` |
| Exams | `exams.view` (teachers: `exams.mark`) | `/exams/`, `/mark-sheets/`, `/results/`, `/term-results/`, `/grades/scales/` |
| Fees | `finance.view` / `finance.collect` | `/fee-structures/`, `/invoices/`, `/payments/`, `/scholarships/` |
| Events | `events.view` | `/events/`, `/point-rules/`, `/awards/` |
| Notices | `notices.manage` (everyone reads) | `/notices/` |
| Messages | staff | `/communication/threads/`, `/communication/appointment-slots/` |
| Support | everyone files; `support.manage` handles | `/support/tickets/` |
| Library | `library.circulate` / `library.manage` | `/library/...` |
| Inventory | `inventory.view` | `/inventory/...` |
| HR | `hr.view`; approvals `hr.approve_leave` | `/hr/...` |
| Payroll | `payroll.view` | `/payroll/...` |
| Hostel | `hostel.view` | `/hostel/...` |
| Transport | `transport.view` | `/transport/...` |
| Applications | `applications.view`; deciders see "Waiting for me" | `/application-types/`, `/applications/`, `/applications/pending/`, `/certificates/` |
| Alumni | `alumni.view` | `/alumni/...` |
| Careers | `careers.view`; job board for all | `/careers/...` |
| Users and roles | `users.view` / `roles.view` | `/users/`, `/roles/`, `/permissions/` |
| API keys | `api_keys.manage` | `/api-keys/` |
| Audit log | `audit.view` | `/audit-logs/` |

**"Waiting for me" inbox.** Put one badge on the dashboard combining
`/applications/pending/`, `/hr/leave-requests/pending/`,
`/mark-sheets/mine/` (teachers), `/attendance/sessions/mine/`, and the
notification count (`/notifications/`).

## 6. Self-service screens ("me")

These need no permission; the server returns only the caller's own data.
For a **parent**, the student `me` endpoints return their child's. With
several children, add `?student=<id>` and show a child switcher (the
children come from `GET /parents/me/`).

| Who | Screens | Endpoints |
|---|---|---|
| Student / parent | Profile, timetable, attendance, exams and admit cards, results, report card, transcript, fees, library, hostel bed and complaints, bus and boarding, events and points, my applications and certificates | `/students/me/`, `/parents/me/`, `/timetable/me/`, `/attendance/records/me/`, `/exams/me/`, `/admit-cards/me/`, `/results/me/`, `/report-cards/me/`, `/transcripts/me/`, `/invoices/me/`, `/library/*/me/`, `/hostel/allocations/me/`, `/hostel/complaints/me/`, `/transport/assignments/me/`, `/transport/trip-records/me/`, `/events/me/`, `/student-points/me/`, `/applications/me/`, `/certificates/me/` |
| Staff / teacher | My timetable, today's roll calls, my mark sheets, my attendance, leave balance and requests, payslips, contract and documents, assets I hold, bus trips I run, interviews I sit on | `/staff/me/`, `/timetable/me/`, `/attendance/sessions/mine/`, `/mark-sheets/mine/`, `/attendance/staff-days/me/`, `/hr/leave-balances/me/`, `/hr/leave-requests/me/`, `/payroll/payslips/me/`, `/hr/contracts/me/`, `/inventory/assets/me/`, `/transport/trips/mine/`, `/careers/interviews/mine/` |
| Alumni | My profile, jobs, studies, achievements; directory; mentors; events; giving; job board | `/alumni/profiles/me/`, `/alumni/employments/`, `/alumni/profiles/directory/`, `/alumni/profiles/mentors/`, `/alumni/events/upcoming/`, `/alumni/campaigns/open/`, `/alumni/donations/me/`, `/careers/postings/` |
| Everyone | Notifications, notices, forms I can fill, open jobs | `/notifications/`, `/notices/`, `/application-types/available/`, `/careers/vacancies/current/` |

## 7. Public pages (no login)

Each organization's public pages use its `code` in the URL
(`/api/v1/public/organizations/{code}/...`). Rate-limited.

- **Admission form**: `application-types/` (the public forms and
  campuses), then `POST applications/`. The response holds a reference
  `number` and a secret `token`. Show both big, tell the applicant to
  save them, and offer a "check status" page: `applications/status/`,
  `resubmit/` (when sent back for changes), `withdraw/`.
- **Careers page**: `careers/vacancies/`, then
  `careers/vacancies/{id}/apply/` (multipart: `data` as a JSON string,
  `resume` the file). It returns `number` and `token` the same way. The
  candidate checks status the same way, and sees and answers an offer
  with `careers/offer/` and `careers/offer/respond/`.

API keys (`Authorization: Api-Key erp_...`) are for the school's other
programs, such as a website. Only an "API keys" settings screen is
needed: it shows the secret once on create and on rotate.

## 8. Workflows to design around

Statuses are fixed (exact values in `openapi.yaml`). Show them as steps
or badges, and offer only the actions the current status allows.

- **Admission**: `pending` → `approved` → `enrolled` (enroll places the
  student in a class), or `rejected` / `withdrawn`.
- **Student**: `active` ⇄ `suspended`, then `graduated` (becomes alumni)
  or `withdrawn`, both final. Moving class is `place`; changing branch is
  `transfer`.
- **Roll call**: open a session → mark → `submitted`. Statuses are present,
  absent, late, excused, leave, medical_leave and on_duty.
- **Exams**: exam `draft` → `scheduled` → `published`. Each mark sheet goes
  `open` (teacher enters) → `submitted` → `verified` (office), and can be
  sent back. Results are visible to students and parents only once
  published. Later changes need a reason.
- **Fees**: generate a term's invoices, `draft` → `issued`; payments
  reduce what's due (`paid_amount`); refunds are separate records; late
  fees are a button, not automatic.
- **Leave**: `pending` → `approved` / `rejected`, or cancelled.
  **Payroll run**: `draft` (compute) → `approved` → `paid`.
- **Applications (any kind)**: `in_review` through ordered steps (each
  step's deciders see it in their inbox) → `approved` (the request is
  carried out: a bed reserved, a leave approved, a certificate issued) /
  `rejected` (reason required) / `returned` to the applicant for changes →
  resubmitted. Show the history timeline (`events` in the detail). Hostel
  approval asks for a bed; a job's final approval asks for an employee
  number.
- **Hiring**: vacancy `draft` → `open` → `closed` / `filled`. A candidate
  moves through the job form's steps; interviews have a panel and an
  outcome; an offer goes `made` → `accepted` / `declined` / `withdrawn`;
  approving the last step with an accepted offer creates the staff
  member.
- **Hostel**: bed `reserved` → `checked_in` → `checked_out`. **Bus trip**:
  open → mark boarded or absent → `completed` (parents are told about an
  absence).
- **Job board**: an alumnus posts (`pending`), the office approves, and it
  shows to students and/or alumni until it closes.

Destructive actions are usually not deletes. Records with history answer
`409` to a delete; offer **deactivate**, **cancel**, **close** or
**revoke** instead, which is the action the API provides.

## 9. Quality bar

- Mobile-first for students, parents and teachers (roll call on a phone).
  Desktop-first for the office (dense tables, keyboard-friendly forms).
- Every list: search, filters, sorting, pagination, an empty state with a
  "create the first one" button, and loading and error states.
- Every form: server errors shown per field, unsaved-change warnings, and
  date pickers that also show BS dates.
- Accessible (labels, contrast, keyboard), translatable (English and
  Nepali strings kept outside the code), dark mode optional.
- Never keep the refresh token where scripts from other sites can read
  it. Keep the access token in memory.
