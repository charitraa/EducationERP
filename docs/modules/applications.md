# Applications

One workflow engine for every request a school handles on paper today:
admission forms, staff leave, scholarships, hostel beds, bus seats, event
places, certificates, and anything else. It lives in
`backend/modules/applications/` and follows the conventions in
[`identity.md`](identity.md). It owns the workflow only. On the final
approval each kind calls the owning module's service, so a hostel bed is
still reserved by hostel's rules and a leave is still approved by HR's.

```
ApplicationType (kind, campus?, public?, extra questions)
   └──► ApprovalStep (sequence, name, permission)          e.g. 1. Class teacher (academics.manage)
                                                                2. Warden (hostel.manage)
Application (type, campus, number, step, status; student | staff | data; contact + token if public)
   └──► ApplicationEvent (submitted | approved | completed | rejected | returned | resubmitted | withdrawn)
   └──► outcome: what approval created, e.g. {"type": "hostel.allocation", "id": 7}

Certificate (student, number, title, frozen contents; revoked, never deleted)
```

## Decisions (agreed before building)

| Question | Choice |
|---|---|
| Engine | **One generic engine.** Each kind has a handler that calls its module's service on final approval. If that action fails (the bed was taken, the bus is full), the approval is refused and nothing changes. |
| Kinds | admission, staff leave, scholarship, hostel, transport, event, certificate (a numbered `Certificate` record), general. |
| Approval chain | **Configurable ordered steps per type.** A step names a permission; anyone holding it for the applicant's campus decides it. Approve, reject (reason required), or send back to the applicant, who fixes and resubmits. The step resumes where it was. Full history. |
| Public forms | **Admission only**, no login, throttled per address. The applicant gets a reference number and a secret token to check, fix or withdraw it. CAPTCHA and email verification belong to the SaaS signup work. |
| Job applications | **Added by the careers module**, plugging into this engine. |

## How a type is set up

- `POST application-types/` with a `kind`, optional `campus` (empty: every
  campus), extra `fields` (questions of type text, textarea, number, date,
  choice or boolean, each optionally required) and `steps`
  (`[{name, permission}]`, in order).
- **The last step must name the permission the handler acts with**:
  `admissions.review` for admission, `hr.approve_leave` for leave,
  `finance.manage` for scholarship, `hostel.manage`, `transport.manage`,
  `events.manage`, `applications.certify` for certificates. Otherwise the
  type is refused (`wrong_final_permission`), because the final approver
  carries the request out with that permission. General requests take any
  chain.
- Steps can't change while an application of the type is open
  (`409 has_open_applications`), so nobody's request jumps to a step that
  no longer means what it did. A type with applications can't be deleted
  (`409 in_use`); deactivate it.
- A campus-scoped office sets up forms for its own campus only. A form for
  every campus needs an organization-wide role.

## Applying and deciding

- `GET application-types/available/` lists forms an applicant can fill in
  (no permissions or internals shown). `POST applications/` submits one.
  It is about the student or staff member asking, a parent's own child, or
  anyone at all when the office (`applications.manage`) applies for them.
  Admission forms need a `campus` instead.
- Each kind's data is validated at submission and again at resubmission:
  ids must belong to the organization, dates must make sense, the event
  must be published, the stop must be on the route, and so on. Answers to
  extra questions are checked against their type.
- `GET applications/pending/` is what waits for me: applications whose
  current step's permission I hold for that campus. `approve`, `reject`
  and `send-back` act on the current step. The row is locked first, so
  two approvers acting at once can't both move it on. A hostel
  application's final approval also needs `decision: {"bed": id}`.
- **Nobody decides their own application**, or their child's
  (`403 own_application`), even when they hold the step's permission.
- `GET applications/me/` lists what I sent or what is about me or my
  children. `resubmit` (after a send-back) and `withdraw` (while open) are
  for the applicant. The office may also withdraw.
- Notifications go through `notifications.services.notify()`: the step's deciders when
  an application reaches them, the applicant (and the student or staff
  member it is about) on approval, rejection or send-back. **A public
  applicant has no account**, so they are emailed or texted at the contact
  they gave, with their reference in the subject.
- Who can open an application: the office (`applications.view` for the
  campus), the applicant or their parent, whoever decides its current
  step, and whoever decided an earlier one. Anyone else gets 404.

## What approval does

| Kind | Calls | Outcome |
|---|---|---|
| admission | `admissions.create_admission` + `approve_admission` | An approved `Admission` with the application's number, ready to enroll from Admissions |
| leave | `hr.apply_leave` + `approve_leave` | An approved `LeaveRequest`; balances and `StaffAttendanceDay` update as in HR |
| scholarship | `finance.grant_scholarship` | A `StudentScholarship` from today (`409 already_granted` if held) |
| hostel | `hostel.allocate_hostel_room` | A reserved `Allocation` on the bed the final approver chose |
| transport | `transport.assign_rider` | An `Assignment` on the route and stop applied for |
| event | `events.register` (+ `decide_registration` for approval events) | A confirmed `EventRegistration` |
| certificate | `issue_certificate` | A numbered `Certificate` |
| general | — | Approved, nothing else |

The module's own checks still apply: campus, capacity, gender, quotas,
and not approving your own leave. Their refusals come back as the usual
`409` / `403` and the application stays at its step.

## Certificates

- Numbered per organization (`CERT-000001`). The facts stated (name,
  number, date of birth, program, level, section, year, campus) are frozen
  in `contents` when issued, so a later transfer or name correction
  doesn't rewrite an issued certificate. Clients render the PDF, as with
  report cards.
- Issued through a certificate application, or directly by the office
  (`POST certificates/`). `revoke` needs a reason; the certificate stays on
  record. `GET certificates/me/` shows a student's or a parent's.

## Public admission

No account, under `/api/v1/public/organizations/{org code}/`:

| | |
|---|---|
| `GET application-types/` | The organization's public forms and active campuses |
| `POST applications/` | Apply with `contact` (name + email or phone). Returns `number` and `token`, shown once and stored only as a SHA-256 hash |
| `POST applications/status/` | `{number, token}`: status and history (step names; only notes addressed to the applicant, on send-back or rejection; never who decided) |
| `POST applications/resubmit/` | After a send-back |
| `POST applications/withdraw/` | While open |

- Limited per address by the `public_applications` throttle
  (`THROTTLE_PUBLIC_APPLICATIONS`, default `20/hour`).
- A wrong number and a wrong token look the same (404), and the token is
  compared in constant time. Another organization's URL can't reach the
  application.
- Only admission types can be public; a database constraint backs it.

## Permissions

| Permission | campus-admin |
|---|:-:|
| `applications.view`: see every application and certificate at a campus | ✓ |
| `applications.manage`: set up forms and chains; apply or withdraw for someone | ✓ |
| `applications.certify`: issue and revoke certificates; decide a certificate application's last step | ✓ |

Deciding a step needs none of these. It needs the permission the step
names, so each office decides its own steps. Applicants need nothing.

## Changes outside the module

- `admissions.services.create_admission` records an admission received
  elsewhere, with a given application number.
- `hr.services.apply_leave(notify_approvers=False)`, used when the leave
  is approved in the same breath, so HR approvers aren't asked to act.
- Development and test SQLite use `transaction_mode: IMMEDIATE`. SQLite
  has no row locks, so concurrent writers wait their turn instead of
  failing with "database is locked".
- `ApplicationKindEnum`, `ApplicationStatusEnum`,
  `ApplicationEventActionEnum` are pinned in `ENUM_NAME_OVERRIDES`. So are
  the inventory enums that had been left with generated names
  (`ConditionEnum`, `AssetMaintenanceKindEnum`,
  `DisposalMethodEnum`). `tests/test_schema.py` now fails on any schema
  warning, so a new collision is caught by the suite.
- The tenant sweep has a row of every new model and attacks for each input
  serializer.

## Testing

- **22 module tests** (`modules/applications/tests`): type setup and step
  rules, the full chain with send-back and resubmit, reject and withdraw,
  required questions, who sees what, parents applying only for their own
  child, each kind's handler (including a taken bed and a full bus
  refusing the approval), certificates issued, revoked and seen by a
  parent, public apply / check / resubmit / approve, refusals,
  emails to a public applicant, and numbers not reused after a delete.
- **Cross-tenant sweep** extended to the new operations (14/14).
- **Live run on a real server** with a realistic college (two campuses,
  +2 / BBS / CSIT, about 75 students), with every module driven by the
  people who would use it: 6397 checks over all 821 operations, zero
  server errors.

## Not built (by choice, for now)

- **CAPTCHA and email verification** on public forms. They come with the
  SaaS public signup work; the throttle is the guard until then.
- **File attachments** (a transcript scan, a medical note). Uploads don't
  exist anywhere yet. They come with the first module that stores files.
- **Parallel steps** ("principal *and* accountant, in any order"). Steps
  are a sequence; put them one after the other.
- **Deadlines and reminders** for a step left waiting. There are no
  background jobs yet, as in earlier phases.
