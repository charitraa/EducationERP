# Education ERP — Backend

A modular education ERP platform (student information system, academics,
finance, HR and campus operations) built as a **modular monolith** on Django +
Django REST Framework.

**Status: Phase 3 complete.** Identity (Phase 1), the student foundation
(Phase 2), the academic structure (Phase 3a: programs, subjects, curriculum,
academic years, sections, teaching assignments, student placement) and the
weekly timetable with clash detection (Phase 3b) are built and tested. Next is
Phase 4, attendance — see [Roadmap](#roadmap).

New here? Start with [How it works, in plain words](#how-it-works-in-plain-words):
organizations vs campuses, roles, and setting up a school with one campus or
several branches.

---

## What Phase 1 delivers

| # | Item | Where |
|---|------|-------|
| 1 | Django project | `backend/config/` |
| 2 | DRF | configured in `config/settings/base.py` |
| 3 | Split settings | `config/settings/{base,development,production,test}.py` |
| 4 | Custom user | `core/accounts/models.py` |
| 5 | Organization | `core/organizations/models.py` |
| 6 | Campus | `core/organizations/models.py` |
| 7 | Roles | `core/permissions/models.py` |
| 8 | Permissions | `core/permissions/{models,registry,selectors}.py` |
| 9 | Authentication | `core/authentication/` (JWT, access + refresh) |
| 10 | Audit log | `core/audit/` |
| 11 | API versioning | `/api/v1/` via `config/api_v1.py` |
| 12 | Testing setup | `tests/` |
| 13 | API documentation | OpenAPI 3 at `/api/docs/` |

## What Phase 2 delivers

| Module | Models | What it does |
|---|---|---|
| `modules/students/` | `Student`, `Enrollment` | Student records; enrollment history; transfer between campuses; suspend / reactivate / graduate / withdraw |
| `modules/parents/` | `Parent`, `StudentParent` | Parents and guardians, linked to students with a relationship and one primary contact |
| `modules/staff/` | `StaffMember` | Staff directory: teaching / non-teaching, designation, joining and leaving |
| `modules/admissions/` | `Admission` | Applications: pending → approved → enrolled (or rejected / withdrawn). Enrolling creates the student and guardian |

Details, rules and design decisions: [`docs/phase-2.md`](docs/phase-2.md).

## What Phase 3a delivers

One model for schools (Grade 1–10), +2 colleges (Grade 11–12) and universities
(Semester 1–8): a **program** has numbered levels, and a **section** is one
class group at one level, for one academic year, at one campus.

| Area | Models |
|---|---|
| Structure (organization-wide) | `Department`, `Program`, `Subject`, `CurriculumSubject`, `AcademicYear`, `Term` |
| Classes (per campus) | `Room`, `Batch`, `Section`, `TeachingAssignment` |
| Placement | `Enrollment.section`: every section a student has been in stays in their history |

Year names are free text, so `2082/83` works; dates are stored in AD.
Details: [`docs/phase-3.md`](docs/phase-3.md).

## What Phase 3b delivers

| Module | Models | What it does |
|---|---|---|
| `modules/timetable/` | `BellSchedule`, `Period`, `TimetableEntry` | Each campus's bell times (several shifts allowed); the weekly timetable of teaching assignments, all year or per term |

A write that would double-book a **teacher** (at any campus), a **room** or a
**section** is refused with `409 timetable_clash` and a list of what it hit.
Overlap is by clock time, so a morning and a day shift are checked against
each other. Elective subjects of one section may run in parallel.

Also in Phase 3:

- **Combined classes**: one teacher, several sections, one room.
- **Hand-over**: move a teacher's lessons to another teacher in one call.
- **Lesson changes**: substitutes, room changes and cancellations on a date,
  and a day view with them applied.
- **`/timetable/me/`**: the timetable for teachers, students and parents.
- **Timetable generator**: fills the week from each assignment's
  `periods_per_week`.
- **Student electives**, and **whole-class promotion**.

Details: [`docs/phase-3b.md`](docs/phase-3b.md) and [`docs/phase-3.md`](docs/phase-3.md).

---

## Quick start

Development uses **MySQL** (or MariaDB) by default. Create an empty database
and a user for it once:

```sql
-- mysql -u root -p   (MariaDB: sudo mariadb)
CREATE DATABASE education_erp CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'education_erp'@'localhost' IDENTIFIED BY 'choose-a-password';
GRANT ALL PRIVILEGES ON education_erp.* TO 'education_erp'@'localhost';
```

```bash
cd backend
source ../.venv/bin/activate          # or: python3 -m venv .venv
pip install -r requirements-dev.txt   # includes the MySQL driver (needs the MySQL/MariaDB client library)

cp .env.example .env                  # then set SECRET_KEY and DB_PASSWORD
python manage.py migrate
python manage.py sync_permissions     # load the permission catalogue + system roles

python manage.py bootstrap_organization \
    --name "Central College" --code central-college \
    --admin-email admin@central.edu --admin-password 'Admin-pass-12345'

python manage.py runserver
```

Then open <http://127.0.0.1:8000/api/docs/>.

To use a different database in development, set `DEV_DATABASE` in `.env`:

| `DEV_DATABASE` | Database | Notes |
|---|---|---|
| `mysql` | MySQL 8.4+ / MariaDB 10.6+ | The default in `.env.example` |
| `postgres` | PostgreSQL | Same as production |
| `sqlite` | file `backend/db.sqlite3` | No server needed; used when `DEV_DATABASE` is missing |

**MySQL limitation.** MySQL can't enforce unique rules that have a condition,
so these rules are missing from the database. (Django's `models.W036` warning
about this is silenced in `development.py` when `DEV_DATABASE=mysql`.)

- a campus code must be unique within its organization
- a role code must be unique within its organization, and among system roles
- a user can't be given the same role twice, for the whole organization or for a campus

The code checks all three before saving (the campus and role serializers, and
the `assign_role` service), so normal use through the API is protected. Rows
written directly, for example `Campus.objects.create(...)` in the shell, are
not checked. Production (PostgreSQL)
enforces all of them. Tests always use in-memory SQLite, which enforces them
too.

```bash
# Log in
curl -X POST http://127.0.0.1:8000/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@central.edu","password":"Admin-pass-12345"}'
```

The response carries `access`, `refresh` and a `user` object that includes the
caller's organization, roles and **resolved permission codes** — enough for a
web or mobile client to render its navigation without a second request.

Send the access token as `Authorization: Bearer <access>`.

### Tests

```bash
cd backend
python manage.py test          # 453 tests, in-memory SQLite
```

`manage.py test` selects `config.settings.test` automatically.

### Running with Docker

This is optional. The venv workflow above needs no services. Docker is for
anyone who wants the full stack (Postgres + Redis + the app) without installing
Python, and it is the same image a deployment runs.

Requirements: Docker Engine with the Compose v2 plugin (`docker compose`).

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and set at least:
#   SECRET_KEY   a long random string
#   DB_PASSWORD  any password; the Postgres container is created with it

make dev        # development: runserver + hot reload, source mounted
# or
make up         # production-shaped: gunicorn, collectstatic, detached
```

Both stacks apply migrations and sync the permission catalogue on start.
The API is on <http://localhost:8000> (change it with `BACKEND_PORT` in `.env`).
Then create the first organization:

```bash
docker compose --env-file ./backend/.env exec backend \
    python manage.py bootstrap_organization \
    --name "Central College" --code central-college \
    --admin-email admin@central.edu --admin-password 'Admin-pass-12345'
```

| Command | What it does |
|---|---|
| `make dev` | Dev stack in the foreground (Postgres, `DEBUG=True`) |
| `make up` / `make down` | Start detached / stop, keeping data |
| `make logs` / `make errors` | Tail app logs / the 500 traceback log |
| `make shell` / `make bash` | Django shell / `sh` inside the container |
| `make migrate` / `make superuser` | Apply migrations / create an admin user |
| `make test` | Run the suite in the container (test settings, no DB needed) |
| `make clean` | Stop and **delete volumes, including the database** |

Without `make`, run the same compose commands yourself. Always pass
`--env-file ./backend/.env`, because Compose otherwise looks for `.env` in the
project root. For the dev stack, also pass
`-f docker-compose.yml -f docker-compose.dev.yml`.

Notes:

- `make up` runs `config.settings.production` over plain HTTP with
  `SECURE_SSL_REDIRECT=False`. In a real deployment, put a TLS-terminating
  proxy in front of it, set `DEBUG=False`, and set `ALLOWED_HOSTS` and
  `CSRF_TRUSTED_ORIGINS` to the real domain.
- Caching: production uses Redis (`REDIS_URL`, required). Development uses a
  file-based cache in `backend/.cache/` (`CACHE_DIR`), so no Redis is needed
  locally. Tests use an in-memory cache. The dev Docker stack still starts the
  `redis` service, but development settings don't use it.
- Postgres and Redis are not published to the host. Uncomment `ports` under `db` in
  `docker-compose.yml` to reach it with a local client.
- With several replicas, set `RUN_MIGRATIONS=false` on all but one of them
  (or on all of them, with migrations run as a separate release step).

---

## How it works, in plain words

This section explains the system without assuming you know Django. For the
technical reasons behind each choice, see [Architecture](#architecture).

### What is in `backend/core/`

| Folder | What it does |
|---|---|
| `organizations/` | The **school or college** itself, and its **campuses** (branches) |
| `accounts/` | **Users**: everyone who can log in (students, teachers, staff, admins) |
| `permissions/` | **Roles** and **permissions**: who is allowed to do what |
| `authentication/` | **Login, logout and tokens** |
| `audit/` | **History**: who did what, and when |
| `common/` | Shared tools used by all of the above: base models, permission checks, error format, health checks |

### The main models

```
Organization  (one school / college)
   │
   ├── Campus        (its branches: "Main Campus", "Lalitpur Campus")
   │
   ├── User          (every person who logs in)
   │     │
   │     └── UserRole ──► Role ──► Permission
   │         (link)      (group)   (one allowed action, e.g. "users.create")
   │
   └── AuditLog      (every change and every login)
```

- **Organization**: name, code, type (school / college / university / institute).
- **Campus**: a branch of one organization. One campus is marked as the main campus.
- **User**: logs in with email and password and belongs to one organization.
  `user_type` (student, teacher, staff…) is only a label. **It does not give
  access. Roles do.**
- **Permission**: one small action, written `module.action`, for example
  `campuses.view` or `users.create`. They are defined in code and loaded with
  `manage.py sync_permissions`.
- **Role**: a named group of permissions. Built in: `org-admin` (everything),
  `campus-admin` (manage users, campuses, students, parents, staff and
  admissions), `staff` (read-only basics), and `student` and `parent`. These
  last two carry no permissions: students and parents reach their own records
  through `/students/me/` and `/parents/me/`, and more comes with later
  phases. Each organization can also create its own roles.
- **UserRole**: gives a role to a user, either for the whole organization or
  for one campus, optionally with an expiry date.
- **AuditLog**: who did it, what they did, what changed (old → new), IP address
  and time. Log entries can never be edited.

Deleting something is a **soft delete**: the record is hidden and marked
`deleted_at`, not removed, so history stays whole.

### Organization vs campus: why both?

They are two different levels:

- **Organization** is the institution. It owns all its data and is completely
  separated from other institutions.
- **Campus** is one location of that institution. One organization can have
  many campuses.

**Example: one college with three branches**

```
Kathmandu Model College          ← Organization
   ├── Baneshwor Campus (main)   ← Campus
   ├── Lalitpur Campus           ← Campus
   └── Bhaktapur Campus          ← Campus
```

- **Shared by the whole college:** one fee policy, one grading system, and a
  principal in charge of all branches. A student who moves from Lalitpur to
  Bhaktapur keeps the same login and history.
- **Different at each branch:** rooms, timetables, attendance, and a local
  branch head who should manage only their own branch.

**Example: two separate colleges on the same server**

```
Organization A: Kathmandu Model College      Organization B: Pokhara Engineering College
   ├── Baneshwor Campus                         └── Main Campus
   └── Lalitpur Campus
```

College A can **never** see College B's data. The organization is a hard wall
between institutions. A campus is a soft division inside one institution.

**Example: a school with only one building**

```
Sunrise Secondary School      ← Organization
   └── Main Campus (main)     ← created automatically
```

This is the normal case. The campus exists but stays invisible in practice.
Roles are given with no campus, and a frontend can hide the campus picker.
Every school has the same shape, so later modules can always say "this room
belongs to a campus". If the school opens a second branch, you just add a
campus; old data stays on Main Campus and nothing has to be moved.

### Who can do what: roles in practice

| Person | Role | Given for | Can manage |
|---|---|---|---|
| Principal | `org-admin` | whole organization | everything, all branches |
| Ram, Lalitpur head | `campus-admin` | Lalitpur Campus | Lalitpur's students, staff, admissions and campus settings |
| Accountant | `staff` | whole organization | read-only basics |
| A student | `student` | their campus | their own record (`/students/me/`) |
| A parent | `parent` | whole organization | their own profile and children (`/parents/me/`) |

A role given **without** a campus counts everywhere in the organization. A role
given **with** a campus counts only there: Ram sees and changes Lalitpur's
campus, students, staff and admissions, and gets 404 for the other branches.

**Nobody can give themselves more power.** You can only grant, revoke or edit a
role whose permissions you already hold, and only at the scope you hold them.
You also can't edit, reset the password of, or deactivate a user who holds
permissions you don't. So Ram can manage teachers and students, but not the
principal. Only the platform superuser can create or delete organizations.

### Setting up a school

#### Step 0: once per server

```bash
python manage.py migrate            # create the tables
python manage.py sync_permissions   # load permissions and built-in roles
```

With Docker, both run automatically every time the container starts.

#### The `bootstrap_organization` command

This adds a new school or college. It solves a chicken-and-egg problem: the API
needs a logged-in admin, but a new school has no users yet. This command creates
the first admin from the server terminal, and after that everything is done
through the API.

It creates three things at once, and if any step fails none of them is saved:

1. the **organization**
2. its first **campus**, marked as main
3. an **admin user** with the `org-admin` role

| Option | Required | Default | Meaning |
|---|---|---|---|
| `--name` | yes | none | School or college name |
| `--code` | yes | none | Short unique id, e.g. `sunrise` |
| `--type` | no | `college` | `school`, `college`, `university`, `institute`, `other` |
| `--campus-name` | no | `Main Campus` | Name of the first campus |
| `--campus-code` | no | `main` | Code of the first campus |
| `--admin-email` | yes | none | Login email of the first admin |
| `--admin-password` | no | random | If omitted, a strong password is generated and **printed once**. Save it. |

It stops with an error if the code is already taken, or if `sync_permissions`
has not been run yet.

With Docker, run it inside the container:

```bash
docker compose --env-file ./backend/.env exec backend python manage.py bootstrap_organization ...
```

#### A school with one campus

```bash
python manage.py bootstrap_organization \
    --name "Sunrise Secondary School" --code sunrise --type school \
    --admin-email principal@sunrise.edu
```

That is all. The principal can now log in and add staff and students.

#### A college with several branches

Run `bootstrap_organization` **once** for the whole college. Name the first
campus after your head branch:

```bash
python manage.py bootstrap_organization \
    --name "Kathmandu Model College" --code kmc --type college \
    --campus-name "Baneshwor Campus" --campus-code baneshwor \
    --admin-email principal@kmc.edu
```

Then the principal adds the other branches through the API:

```bash
# 1. Log in. Copy "access" from the response.
curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -d '{"email": "principal@kmc.edu", "password": "..."}'

TOKEN=<access token>

# 2. Add branches. The organization is taken from your login, never from the body.
curl -X POST http://localhost:8000/api/v1/campuses/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "Lalitpur Campus", "code": "lalitpur", "city": "Lalitpur"}'

curl -X POST http://localhost:8000/api/v1/campuses/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "Bhaktapur Campus", "code": "bhaktapur", "city": "Bhaktapur"}'
```

Then add a head for each branch, and give them `campus-admin` **for their
branch only**:

```bash
# 3. Create the user. Note the "id" in the response.
curl -X POST http://localhost:8000/api/v1/users/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"email": "ram@kmc.edu", "first_name": "Ram", "user_type": "staff", "password": "..."}'

# 4. Find the ids you need.
curl "http://localhost:8000/api/v1/roles/?search=campus-admin" -H "Authorization: Bearer $TOKEN"
curl "http://localhost:8000/api/v1/campuses/?search=lalitpur"  -H "Authorization: Bearer $TOKEN"

# 5. Give the role for that campus.
curl -X POST http://localhost:8000/api/v1/users/<ram_id>/assign-role/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"role": <campus_admin_role_id>, "campus": <lalitpur_campus_id>}'
```

Leave out `"campus"` to give a role for the whole organization. Add
`"expires_at"` for a temporary role. Everything above can also be done from the
interactive docs page at `/api/docs/`.

**Adding a campus needs no migration.** Migrations change the *shape* of the
database (new tables or columns) and only happen when the code changes. A new
campus, user or role is just a new row.

| | One campus | Several branches |
|---|---|---|
| `bootstrap_organization` | once | once |
| Extra campuses | none | `POST /api/v1/campuses/` per branch |
| Roles | given with no campus | branch heads get a role **with** their campus |
| Migrations | none | none |

### What happens on every API request

```
Client sends:  Authorization: Bearer <access token>
        │
        ▼
 ① Who are you?    The token is checked and the user is found
        ▼
 ② Allowed?        user → roles → permissions.
                   Does the user hold the code this endpoint needs
                   (e.g. "campuses.create")?  No → 403
        ▼
 ③ Your data only  Results are filtered to the user's own organization.
                   Another organization's record returns 404, as if it did not exist
        ▼
 ④ Do the work     The view calls a service function, where the rules live
        ▼
 ⑤ Record it       Every create / update / delete is written to the audit log
        ▼
 JSON response.  Errors always look like {"error": {"code", "message", "details"}}
```

**Login:** `POST /auth/login/` returns an **access** token (valid 60 minutes),
a **refresh** token (7 days) and the user's roles and permissions. When the
access token expires, `POST /auth/refresh/` returns a new one.
`POST /auth/logout/` blocks the refresh token. Every login, failed login and
logout is written to the audit log.

### Security

| Protection | What it does | Setting |
|---|---|---|
| Login rate limit | 10 login attempts a minute per client address | `THROTTLE_LOGIN` |
| Account lockout | 5 failures on one account, from any address, lock it for 15 minutes. The right password is refused too. Unknown emails lock the same way, so nothing is revealed | `LOGIN_LOCKOUT_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES` |
| API rate limit | 60 requests/min per address when logged out, 600/min per user when logged in. `/health/` and `/ready/` are exempt | `THROTTLE_ANON`, `THROTTLE_USER` |
| Two-factor login | Optional authenticator-app codes, with 10 single-use recovery codes. Also asked for at `/admin/` | `/auth/2fa/…` endpoints below |
| Admin login | Same lockout and two-factor step as the API | — |
| Client IP | `X-Forwarded-For` is trusted only for the proxies you declare, so clients can't fake their address to dodge limits or pollute the audit log | `NUM_PROXIES` |
| Secret key | Production refuses to start on a missing, short or placeholder `SECRET_KEY` | `SECRET_KEY` |
| API docs | Staff-only in production (log in at `/admin/` first) | `API_DOCS_PUBLIC` |
| Content-Security-Policy | Enforced in production, report-only in development. Docs files are served by us, not a CDN | `CSP_POLICY` in `base.py` |
| HTTPS | HSTS, HTTPS redirect, secure cookies, no framing, no-sniff. `manage.py check --deploy` passes | `SECURE_*` |
| Dependencies | `pip-audit -r requirements.txt` checks packages against known vulnerabilities | — |

**Setting `NUM_PROXIES` correctly matters.** It is the number of proxies (load
balancer, nginx) between the internet and the app. Use `0` when clients connect
directly, as on a laptop or with the Docker stack as shipped. If it is higher
than the real number, clients can fake their IP again.

**Turning on two-factor login** (for a frontend to build):

1. `POST /auth/2fa/setup/` with `{"password": …}` returns `otpauth_uri`. Show it as a QR code.
2. The user scans it and sends a code: `POST /auth/2fa/confirm/` with `{"code": "123456"}`.
   The response has 10 recovery codes. Show them once.
3. From then on, `POST /auth/login/` needs `"otp"` too. Without it the answer
   is `400 otp_required`, so ask for the code and send everything again.

A lost phone: log in with a recovery code, or an admin calls
`POST /users/{id}/reset-2fa/` (same rule as a password reset: only for users
no more powerful than the admin).

### Known gaps

- **Users and roles are organization-wide, not per campus.** Campus scoping
  covers campus-owned records (campuses, students, staff, admissions). A
  campus-scoped admin can still list every user in the organization, and
  manage those no more powerful than themselves.
- **Numbers are typed in, not generated.** Student, employee and application
  numbers must be supplied; the API rejects duplicates. Automatic numbering
  can come once institutions agree on a format.
- **Admissions are recorded by staff.** There is no public application form
  yet. That arrives with the Applications phase.
- **Two-factor is optional.** Nothing forces admins to turn it on yet. The
  authenticator secret is stored readable in the database, as TOTP needs it
  to check codes; encrypting it at rest would need a separate key.
- **File uploads** don't exist yet. Size, type and access checks come with
  the first module that stores documents or photos.

---

## API surface (v1)

```
POST   /api/v1/auth/login/                 email + password -> token pair + profile
POST   /api/v1/auth/refresh/               refresh -> new access token
POST   /api/v1/auth/logout/                blacklist a refresh token
GET    /api/v1/auth/me/                    current user, roles, permissions
PATCH  /api/v1/auth/me/                    update own contact details
POST   /api/v1/auth/change-password/
GET    /api/v1/auth/2fa/                   two-factor status
POST   /api/v1/auth/2fa/setup/             {password} -> secret + otpauth URI
POST   /api/v1/auth/2fa/confirm/           {code} -> turns it on, returns recovery codes
POST   /api/v1/auth/2fa/recovery-codes/    {code} -> new set of recovery codes
POST   /api/v1/auth/2fa/disable/           {password, code}

GET    /api/v1/organizations/              view/update own; create/delete: platform superuser only
GET    /api/v1/campuses/                   CRUD (tenant- and campus-scoped)

GET    /api/v1/users/                      CRUD (tenant-scoped)
GET    /api/v1/users/{id}/roles/
POST   /api/v1/users/{id}/assign-role/
POST   /api/v1/users/{id}/revoke-role/
POST   /api/v1/users/{id}/set-password/
POST   /api/v1/users/{id}/deactivate/
POST   /api/v1/users/{id}/reset-2fa/       turn off a user's two-factor (lost phone)

GET    /api/v1/permissions/                read-only catalogue
GET    /api/v1/roles/                      CRUD (system roles are read-only)

GET    /api/v1/audit-logs/                 read-only trail

GET    /api/v1/students/                   CRUD (campus-scoped; create opens the first enrollment)
GET    /api/v1/students/me/                the caller's own student record
GET    /api/v1/students/{id}/enrollments/  enrollment history
POST   /api/v1/students/{id}/transfer/     move to another campus
POST   /api/v1/students/{id}/change-status/  suspend / reactivate / graduate / withdraw

GET    /api/v1/parents/                    CRUD (?student=<id> for a student's parents)
GET    /api/v1/parents/me/                 the caller's own profile and children
GET    /api/v1/parents/{id}/students/
POST   /api/v1/parents/{id}/link-student/
POST   /api/v1/parents/{id}/unlink-student/

GET    /api/v1/staff/                      CRUD (campus-scoped)
GET    /api/v1/staff/me/                   the caller's own staff record

GET    /api/v1/admissions/                 CRUD (campus-scoped; edit only while pending)
POST   /api/v1/admissions/{id}/approve/
POST   /api/v1/admissions/{id}/reject/     note required
POST   /api/v1/admissions/{id}/withdraw/
POST   /api/v1/admissions/{id}/enroll/     creates the student (+ guardian)

GET    /api/v1/departments/  programs/  subjects/  curriculum/      CRUD (organization-wide)
GET    /api/v1/academic-years/  terms/                              CRUD; POST academic-years/{id}/set-current/
GET    /api/v1/rooms/  batches/  sections/  teaching-assignments/   CRUD (campus-scoped)
GET    /api/v1/sections/{id}/students/                              who is in a class (?subject= who takes it)
POST   /api/v1/sections/{id}/promote/                               move a whole class (all or nothing)
GET    /api/v1/student-electives/                                   CRUD (no edit): who takes which elective
POST   /api/v1/students/{id}/place/                                 place / promote / move a student

GET    /api/v1/bell-schedules/  periods/                            CRUD (campus-scoped)
GET    /api/v1/timetable/                                           CRUD (campus-scoped); 409 on clashes
GET    /api/v1/timetable/?section=|teacher=|room=                   one class's, teacher's or room's week
GET    /api/v1/timetable/?date=YYYY-MM-DD                           the lessons of one day
GET    /api/v1/timetable/day/?date=                                 one day with substitutes, room changes, cancellations
GET    /api/v1/timetable/me/                                        own timetable (teacher, student or parent)
POST   /api/v1/timetable/hand-over/                                 give lessons to another teacher
POST   /api/v1/timetable/generate/                                  fill the week automatically (dry run by default)
GET    /api/v1/lesson-changes/                                      CRUD: one lesson on one date

GET    /health/                            liveness
GET    /ready/                             readiness (checks the database and cache)
GET    /api/schema/  /api/docs/  /api/redoc/   (staff-only unless API_DOCS_PUBLIC=True)
```

Every error uses one envelope:

```json
{"error": {"code": "permission_denied", "message": "...", "details": null}}
```

---

## Architecture

```
backend/
├── config/            project settings, URLs, API version routing
├── core/              Phase 1 — the identity foundation
│   ├── common/        abstract models, permissions, mixins, pagination, errors
│   ├── organizations/ Organization, Campus
│   ├── accounts/      User + user services
│   ├── authentication/ JWT login, refresh, logout, me, change-password
│   ├── permissions/   Permission, Role, UserRole + registry and selectors
│   └── audit/         AuditLog, context middleware, audit services
├── modules/           business modules, Phase 2 onward
│   ├── students/      Student, Enrollment
│   ├── parents/       Parent, StudentParent
│   ├── staff/         StaffMember
│   ├── admissions/    Admission
│   ├── academics/     Department, Program, Subject, curriculum, years, terms,
│   │                  Room, Batch, Section, TeachingAssignment
│   └── timetable/     BellSchedule, Period, TimetableEntry, LessonChange,
│                      clash detection, generator
├── integrations/      biometric, payment, SMS, email, push (empty)
└── tests/             shared factories, base test case, cross-cutting tests
```

Each app follows the same layout: `models` → `serializers` → `services`
(writes) / `selectors` (reads) → `views` → `urls` → `tests`.

### Five decisions worth knowing

**1. One identity, many profiles.** Students, parents, teachers and staff all
authenticate through a single `User`. Phase 2's `Student`, `Parent` and
`StaffMember` records each point back at one optional `User` (a young
student may have no login) — never a second login system. `user_type` is a
broad label; access always comes from roles.

**2. Permissions are declared in code, not typed into a database.**
`core/permissions/registry.py` is the catalogue; `manage.py sync_permissions`
writes it to the database idempotently. Views declare what they need:

```python
class CampusViewSet(OrganizationScopedViewSet):
    required_permissions = {
        "list": ["campuses.view"],
        "create": ["campuses.create"],
    }
```

An action with no declared permission is **closed**, not open. Adding a module
means adding its `PermissionSpec`s — no permission strings scattered through
view bodies.

**3. Multi-tenancy lives in the queryset.** Every tenant-owned model carries
`organization`, and `OrganizationScopedMixin` narrows the queryset before the
view ever runs. The tenant is taken from the authenticated user, so an
`organization` field in a request body is ignored. Another tenant's row returns
**404, not 403** — existence is not disclosed. `IsSameOrganization` re-checks
at object level as defence in depth.

**4. Soft delete for records, immutable history for the audit trail.**
`BaseModel` gives `created_at`, `updated_at`, `deleted_at`, `deleted_by`, and
`Model.objects` hides deleted rows while `Model.all_objects` shows them. Unique
constraints are partial (`condition=Q(deleted_at__isnull=True)`), so deleting a
campus frees its code. `AuditLog` is deliberately *not* a `BaseModel`: it
rejects any update. Financial corrections in later phases must be adjustments
or reversals, never edits.

**5. Services and selectors, not fat views.** Business rules live in
`services.py` (writes) and `selectors.py` (reads) and raise `ServiceError`
subclasses rather than HTTP exceptions, so the domain layer stays callable from
management commands, Celery tasks and other modules. Modules talk to each other
through these functions, never by reaching into another module's tables — which
is what keeps a future service extraction possible.

### Database

SQLite in development, PostgreSQL in production, through the ORM only. No
raw SQL and no backend-specific constructs — `UserRole`'s uniqueness uses two
partial constraints rather than PostgreSQL-only `nulls_distinct`, so dev and
prod behave identically.

---

## Roadmap

Phases 1, 2 and 3 are done. The order below is the **dependency order**: each
phase only needs the ones before it, so nothing has to be built on
placeholders.

| Phase | Scope | Needs |
|-------|-------|-------|
| ~~1~~ | Identity: organizations, campuses, users, roles, permissions, audit | — |
| ~~2~~ | Students, parents, staff, admissions, enrollment | 1 |
| ~~3a~~ | Academics: departments, programs, subjects, curriculum, years, terms, batches, sections, rooms, teaching assignments, placement | 2 (teachers, enrollment) |
| ~~3b~~ | Timetable: periods, weekly schedule, clash detection | 3a |
| **4** | Attendance: one engine for manual / QR / biometric | 3 (a class and timetable to take attendance in) |
| 5 | Examinations: exams, marks, grades, results, transcripts | 3 (subjects, syllabus) |
| 6 | Finance: fee structures, invoices, payments, scholarships, refunds | 2 and 3 (fees per program) |
| 7 | Events and student points | 2 |
| 8 | Communication: notices, notifications, support tickets | 2 |
| 9–10 | Library, inventory | 2 |
| 11 | HR and payroll (extends `StaffMember`) | 2 |
| 12 | Hostel and transport | 2, 6 |
| 13 | Applications (public admission forms and other workflows) | 2 |
| 14 | Alumni and careers (uses the graduated status) | 2 |

See [`docs/phase-1.md`](docs/phase-1.md), [`docs/phase-2.md`](docs/phase-2.md),
[`docs/phase-3.md`](docs/phase-3.md) and [`docs/phase-3b.md`](docs/phase-3b.md) for the data model and the conventions
every module follows.
