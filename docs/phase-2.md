# Phase 2 — Student Foundation

Students, enrollments, parents, staff and admissions. Everything here follows
the conventions in [`phase-1.md`](phase-1.md).

---

## Data model

```
Organization
   │
   ├── Campus ◄──────────────┬──────────────┬─────────────────┐
   │                         │              │                 │
   ├── Student ──────────────┘              │                 │
   │     │  user? ─► User                   │                 │
   │     ├── Enrollment ─► Campus           │                 │
   │     └── StudentParent ◄── Parent       │                 │
   │                            user? ─► User                 │
   ├── StaffMember ─────────────────────────┘                 │
   │     user? ─► User                                        │
   └── Admission ─────────────────────────────────────────────┘
         student? ─► Student   (set on enrollment)
```

Every model carries `organization`. Campus-owned models (`Student`,
`Enrollment`, `StaffMember`, `Admission`) also carry `campus`; `Parent` is
organization-level because one family can have children at several campuses.

### Student (`modules/students`)
The source of truth for who a learner is. `user` is an optional link to a
login account: young students often have none, so the record keeps its own
legal name rather than borrowing the account's.

- `student_number` unique per organization among non-deleted students.
- `status`: active, suspended, graduated, withdrawn. Changed only through
  `change_student_status`.
- `campus`: where the student studies now. Changed only through
  `transfer_student`.
- `admitted_on`: fixed after creation, because the first enrollment starts then.

### Enrollment (`modules/students`)
One continuous period of study at a campus. History, not state: rows are
closed, never deleted, so the student's path can be rebuilt.

Invariant, maintained by the services and backed by the database:

| Student status | Open enrollments |
|---|---|
| active, suspended | exactly one, at the student's campus |
| graduated, withdrawn | none |

Constraints: one `active` enrollment per student (partial unique); `ended_on`
is set exactly when the status is not active; `ended_on >= started_on`.

Phase 3 adds the academic placement (academic year, program, batch, section)
to this model. That is why it exists already, rather than being folded into
`Student`.

### Parent and StudentParent (`modules/parents`)
A parent or guardian, and the links saying whose. `relationship` is father,
mother, guardian or other. At most one link per student is the primary
contact; making a new one primary unsets the old one in the same transaction.

### StaffMember (`modules/staff`)
The staff directory. `designation` is free text ("HOD", "Driver") so each
institution names its own positions without schema changes. `status` is
active, on leave or left; `left_on` is set exactly when the status is left
(check constraint). HR (Phase 11) extends this record; it doesn't replace it.

### Admission (`modules/admissions`)
An application, from receipt to enrollment:

```
pending ──approve──► approved ──enroll──► enrolled
   │                    │
   ├──reject──► rejected│   (a reason is required)
   └──withdraw──────────┴──► withdrawn
```

Applicant and guardian details are stored as submitted. Enrolling copies them
into a new `Student` (plus `Parent` and a primary `StudentParent` link when a
guardian was given) through those modules' services, all in one transaction.
The application is editable only while pending, and cannot be deleted once
approved or enrolled. `status = enrolled` exactly when `student` is set
(check constraint).

---

## Permissions and roles

| Permission | campus-admin | staff |
|---|:-:|:-:|
| `students.view` | ✓ | ✓ |
| `students.create` / `.update` | ✓ | |
| `students.change_status` (transfer, suspend, graduate, withdraw) | ✓ | |
| `students.delete` | | |
| `parents.view` | ✓ | ✓ |
| `parents.create` / `.update` (includes linking) | ✓ | |
| `parents.delete` | | |
| `staff.view` | ✓ | ✓ |
| `staff.create` / `.update` | ✓ | |
| `staff.delete` | | |
| `admissions.view` / `.create` / `.update` | ✓ | |
| `admissions.review` (approve, reject) | ✓ | |
| `admissions.enroll` | ✓ | |
| `admissions.delete` | | |

`org-admin` holds everything. Deletes are left to org-admin on purpose.
Enrolling an applicant needs both `admissions.enroll` and `students.create`.

Each module declares its permissions in its own `permissions.py`, loaded from
`AppConfig.ready()`, and adds them to system roles with
`grant_to_system_role()`, so a module's access rules live beside its code.

**Self-service** endpoints need no permission. Being the person the record is
about is the authorization:

- `GET /students/me/`: the student record linked to the caller
- `GET /parents/me/`: the caller's parent profile and their children
- `GET /staff/me/`: the caller's staff record

The `student` and `parent` roles therefore stay empty. A student or parent
holding no permissions also means any user manager can manage their accounts
(see `ensure_can_manage_user`).

---

## Campus scoping

`CampusScopedViewSet` (in `core/common/mixins.py`) narrows every query to the
campuses where the caller holds the action's permission:

- Organization-wide role: every campus.
- Role granted for Lalitpur: only Lalitpur's rows. Others are 404, the same as
  another organization's rows.
- Writes are checked too: creating at, or transferring to, a campus the role
  doesn't cover is 403.

Parents are organization-level, but a parent's children are listed only if
the caller can see those students, and linking needs the student to be
visible.

---

## Design decisions

- **Enrollment without academics.** Phase 3 owns programs and batches. Phase 2's
  enrollment records the *where and when*, so Phase 3 only adds columns
  instead of reshaping data.
- **Alumni is not a user type.** Graduation is a status on `Student`. The
  alumni module (Phase 14) builds on it, with the same person and login.
- **Numbers are supplied, not generated.** Duplicates are rejected with a 400
  before the database is reached, which also covers MySQL, where partial
  unique constraints aren't enforced.
- **Services write the audit trail** for students, transfers, status changes,
  links and admission decisions, so every path is audited, not just the API.
  Viewsets whose create goes through such a service set
  `service_audits_create = True` so nothing is logged twice.
- **Concurrency.** Status changes, transfers, admission decisions and enrolling
  lock the row first (`select_for_update`). Two people approving at once, or a
  double-clicked "enroll", cannot create two students.

---

## Test coverage

About 90 tests across the four modules (266 in the whole suite): database
constraints and invariants, CRUD, validation messages, every workflow
transition, all-or-nothing enrollment, permission boundaries per action,
tenant isolation, campus scoping (read, create, transfer), self-service
endpoints, and audit entries. Query counts on the list endpoints stay constant
as rows grow (no N+1 queries).

---

## Known limitations

- Users and roles are organization-wide; campus scoping applies to
  campus-owned records only.
- No public admission form. Applications are entered by staff until the
  Applications phase.
- No document uploads (birth certificates, photos). These wait for file
  storage (`core/files/`).
- Student, employee and application numbers are typed in.
