# Education ERP — Backend

**One system to run a school, +2 college or university**: students,
classes, timetables, attendance, exams, fees, staff and payroll, library,
hostel, transport, communication and alumni, all in one place. It is the
backend: a REST API (Django + Django REST Framework) that a web or mobile
app is built on.

It is **free to use**. A school signs itself up, verifies its email and
starts working. Many schools share one server, and each one only ever sees
its own data. A school with several branches runs them all from one
account.

Building the UI? Read [`docs/frontend-brief.md`](docs/frontend-brief.md), with the
endpoint list in [`docs/api/`](docs/api/).

New here? Start with [How it works, in plain words](#how-it-works-in-plain-words):
organizations vs campuses, roles, and setting up a school with one campus or
several branches.

---

## Who uses it

| Person | What they do in it |
|---|---|
| Principal, office admin | Set up the school, its branches, classes, fees and staff; see everything |
| Branch head | Run one branch: its students, staff, admissions and attendance |
| Teacher | Take attendance, enter marks, see their own timetable, message parents |
| Accountant | Bill fees, record payments, run payroll |
| Librarian, storekeeper, warden, transport officer | Run the library, stores, hostel or buses |
| Student | Their own timetable, attendance, results, fees, applications and library books |
| Parent | The same for each of their children; messages and appointments with teachers |
| Graduate | Their alumni profile, reunions, mentoring, donations and the job board |
| Applicant (no account) | Apply for admission or a job online and follow the application |

What a person can do comes from the **roles** they are given, not from
their job title. Each school can make its own roles.

## What it does

### Students and admissions

- Student records, parents and guardians, and the staff directory.
- Admissions: apply (online, without an account, or at the office) →
  approved → enrolled. Enrolling creates the student and their guardian.
- Transfer between branches, suspend, graduate or withdraw. The full
  history of every class a student has been in is kept.

### Classes and timetable

- One shape for every kind of institution: a **program** (Grade 1–10,
  +2 Science, BBA) has levels (grades or semesters), and a **section** is
  one class group in one year at one branch. Year names are free text, so
  `2082/83` works.
- Subjects, curriculum, electives, rooms, class teachers and who teaches what.
- A weekly **timetable** that refuses to double-book a teacher, a room or
  a class, with an automatic generator, substitutes, room changes,
  cancellations and combined classes.
- An academic calendar: holidays, closures, exam days, make-up days.
- Whole-class promotion to the next year.

### Attendance

- Students: a daily roll call (schools) or attendance in every lesson
  (colleges), taken by the right teacher, including a substitute.
- Staff: check-in and check-out by **biometric device** (ZKTeco or any
  device through a generic API), a gate **QR code** or the office; late
  arrivals and half days against each person's schedule.
- QR attendance for students that is hard to cheat: codes expire in about a
  minute, an optional location check, one phone per student.
- Works offline: a teacher's app can sync later without counting twice.
- Reports: percentage per student and subject, the class register, students
  below 75%, classes not taken today.

### Exams and results

- Exams with theory, practical and internal parts, an exam timetable
  checked for clashes, seat plans across rooms and invigilator duty.
- Admit cards, withheld automatically below an attendance minimum.
- Marks: teacher enters → submits → office verifies. Changing a mark
  afterwards needs a reason and is kept on record.
- Grading set by the school: percentage, letter grades, GPA, divisions,
  pass/fail. Results with class rank, term results that combine several
  exams, report cards and transcripts.

### Fees

- Fee structures per program, level and year: one-time (admission) and
  per-term (tuition) items.
- Invoices generated for a whole term in one step; scholarships applied
  automatically; discounts, fines, late fees and installments.
- Payments with numbered receipts, and refunds. Money that has moved is
  never edited: a correction is a new entry.
- Hostel and bus fees bill through the same system.

### Staff, HR and payroll

- Contracts, positions, staff documents with expiry dates.
- Leave: yearly quotas per leave type, carry-forward, apply → approve.
  Approved leave shows in staff attendance.
- Salary structures (basic plus allowances and deductions), set per person
  from a date.
- Income tax from the school's own yearly slabs (Nepal's rules work, nothing
  is hardcoded for one country), with PF, CIT and SSF.
- Monthly payroll per branch from salary, leave and attendance (unpaid
  days, overtime): draft → approved → paid, with payslips.

### Communication

- Notices for chosen groups (students, parents, staff, alumni) at one
  branch or all of them.
- In-app notifications, plus email, SMS and push, sent by every part of
  the system (a payment received, results published, a book ready…).
- Messages between staff and students or parents, appointment booking, and
  support tickets.

### Applications and certificates

- One online request system for admissions, leave, scholarships, hostel
  beds, bus seats, event places, certificates and job applications.
- Each kind of request has its own form and its own approval steps.
  Approving it carries it out (the bed is reserved, the leave recorded, the
  certificate issued); if that can't happen, nothing changes.
- Certificates are numbered and can be revoked, never deleted.

### Campus life

- **Events**: registration, check-in, participation and competition
  results; points, badges, achievements and titles awarded by rules.
- **Library**: catalog, copies on shelves, members, issue and return,
  reservations queue, overdue and lost-book fines.
- **Inventory**: stores at each branch, stock in and out, purchase orders
  and deliveries, low-stock alerts, assets with tags, maintenance and
  disposal.
- **Hostel**: buildings, rooms and beds; reserve, check in, move and check
  out; complaints.
- **Transport**: vehicles and their papers, drivers, routes and stops,
  riders; the bus crew marks each pickup and drop, and parents are told if
  their child is absent.

### Alumni and careers

- Graduating students become alumni and keep their login: profile, jobs,
  higher studies, an opt-in directory.
- Reunions, mentoring, fundraising campaigns and donations with receipts.
- The school's own hiring: vacancies, online applications, interviews,
  offers. Accepting an offer creates the staff record and contract.
- A job board of outside openings for students and alumni.

### For the school's IT and other software

- **Self-signup**: a school creates its own account online, with email
  verification, CAPTCHA and forgot-password.
- **API keys** so other programs (an SMS gateway, a website, a device) can
  use the API with only the access they are given.
- Two-factor login, account lockout, rate limits, and an audit log of every
  change and every login.
- Private file uploads (PDF, Word, images) that only the right people can
  download.

## Built so that

- **Schools never see each other's data.** Every request is limited to the
  signed-in person's school, and an automated test attacks every endpoint
  from another school to prove it.
- **History is never rewritten.** Class moves, timetable changes, marks,
  payments and payslips take effect from a date or are corrected with a new
  entry, so last year's reports still come out the same.
- **Nothing is silently half-done.** A mark sheet with missing marks gives
  no grade; an approval that can't be carried out is refused.
- **It fits real institutions.** Bikram Sambat year names, Nepali tax
  slabs, morning and day shifts, several branches, schools and universities,
  all as settings rather than code.

Every area has a detailed document (data model, rules and the decisions
behind them) in [`docs/`](docs/): see [Detailed documentation](#detailed-documentation).

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
pip install -r requirements.txt       # includes the MySQL driver (needs the MySQL/MariaDB client library)

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
python manage.py test          # in-memory SQLite
```

`manage.py test` selects `config.settings.test` automatically.

`tests/test_tenant_sweep.py` attacks every `/api/v1/` route from one
organization against another: foreign ids in the URL, in filters and in
request bodies. New endpoints are covered automatically. When one can't be
(a new model, or an action with ids in its body), a guard test fails and
says what to add to `build_tenant()`, `ACTION_ATTACKS` or `NO_RECORD_INPUT`.

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
  `campus-admin` (manage users, campuses, students, parents, staff,
  admissions and attendance), `staff` (read-only basics, plus taking
  attendance for their own classes), and `student` and `parent`. These
  last two carry no permissions: students and parents reach their own records
  through `/students/me/` and `/parents/me/` and the other `/me/` endpoints. Each organization can also create its own roles.
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

**Programs** use an API key instead: `Authorization: Api-Key erp_...`. A key
has its own roles, like a person — see [`docs/modules/api-keys.md`](docs/modules/api-keys.md).

**Login:** `POST /auth/login/` returns an **access** token (valid 60 minutes),
a **refresh** token (7 days) and the user's roles and permissions. When the
access token expires, `POST /auth/refresh/` returns a new one.
`POST /auth/logout/` blocks the refresh token. Every login, failed login and
logout is written to the audit log.

**Forgot password:** `POST /auth/password-reset/` emails a link, and
`POST /auth/password-reset/confirm/` sets the new password and signs the
account out everywhere. **New organizations** can sign themselves up
(`POST /signup/`, when `SIGNUP_ENABLED` is on): the organization is created
once the emailed link is opened. See [`docs/modules/signup.md`](docs/modules/signup.md).

### Security

| Protection | What it does | Setting |
|---|---|---|
| Login rate limit | 10 login attempts a minute per client address | `THROTTLE_LOGIN` |
| Signup and reset | Off unless `SIGNUP_ENABLED`; a CAPTCHA (Turnstile, hCaptcha or reCAPTCHA) required in production; nothing created until the emailed link is opened; throwaway email domains refused; same answer whether or not an account exists; at most 5 mails an hour to one address | `SIGNUP_*`, `CAPTCHA_*`, `EMAILS_PER_ADDRESS_PER_HOUR` |
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
- **Public admission forms don't verify the applicant's email.** They
  check a CAPTCHA once one is configured (`CAPTCHA_PROVIDER`), and a
  per-address throttle applies.
- **Two-factor is optional.** Nothing forces admins to turn it on yet. The
  authenticator secret is stored readable in the database, as TOTP needs it
  to check codes; encrypting it at rest would need a separate key.
- **Uploads aren't virus-scanned.** Only PDF, Word (no macros) and images
  are accepted, decided from the bytes, and they are served as downloads,
  never inline. A scanner belongs behind the storage adapter.
- **ZKTeco devices have no password.** Their push protocol names a device
  by serial number only. Set `allowed_ips` on every ZKTeco device; the
  generic device API uses a proper key instead.

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
POST   /api/v1/bell-schedules/{id}/retime/                          new bell times from a date
GET    /api/v1/calendar/                                            CRUD: holidays, closures, exams, make-up days

POST   /api/v1/attendance/sessions/                                 open a lesson's or class's attendance (idempotent)
GET    /api/v1/attendance/sessions/mine/                            a teacher's classes to take today
GET    /api/v1/attendance/sessions/{id}/roster/                     who is expected, and their marks
POST   /api/v1/attendance/sessions/{id}/mark/  submit/  reopen/  qr/
POST   /api/v1/attendance/sessions/scan/                            students scan the class QR
GET    /api/v1/attendance/records/                                  list; PATCH corrects (reason once submitted)
GET    /api/v1/attendance/records/me/                               own attendance (students), a child's (parents)
GET    /api/v1/attendance/reports/student/  register/  defaulters/  missing/  staff/
GET    /api/v1/attendance/staff-days/  punches/                     staff days (set by hand), raw punches
POST   /api/v1/attendance/punches/qr/  punches/check-in/            gate QR for staff
GET    /api/v1/attendance/work-schedules/  staff-schedules/  devices/  biometric-ids/   CRUD
POST   /api/v1/attendance/device-punches/                           devices: Authorization: Device <key>
GET    /iclock/cdata  /iclock/getrequest                            ZKTeco push protocol

GET    /api/v1/grades/scales/                                       CRUD; GET {id}/grade/?percentage=
GET    /api/v1/exam-types/  exam-subjects/  exam-rooms/              CRUD (campus-scoped)
GET    /api/v1/exams/                                                CRUD (draft only deletable)
POST   /api/v1/exams/{id}/add-curriculum/  schedule/  unschedule/    build and lock the exam's papers
GET    /api/v1/exams/{id}/readiness/  summary/                       what blocks publishing; class results
POST   /api/v1/exams/{id}/compute/  publish/  unpublish/             results, unpublished / published
POST   /api/v1/exams/{id}/seat-plan/  generate-admit-cards/          interleave/sequential; withheld below attendance
GET    /api/v1/exams/me/                                             a student's/parent's own exams
GET    /api/v1/seat-allocations/  invigilations/  admit-cards/       CRUD (campus-scoped); admit-cards/{id}/data/
GET    /api/v1/mark-sheets/                                          list/retrieve; POST opens one (idempotent)
GET    /api/v1/mark-sheets/mine/  {id}/roster/
POST   /api/v1/mark-sheets/{id}/marks/  submit/  verify/  send-back/
GET    /api/v1/marks/                                                 list/retrieve; PATCH corrects (reason once locked)
GET    /api/v1/results/                                                list/retrieve; {id}/report-card/; POST {id}/remark/
GET    /api/v1/results/me/  report-cards/  report-cards/me/  transcripts/{student_id}/  transcripts/me/
GET    /api/v1/term-results/                                          CRUD; POST compute/  publish/  unpublish/

GET    /api/v1/fee-categories/  fee-structures/                       CRUD (items locked once invoiced)
POST   /api/v1/fee-structures/{id}/generate-invoices/  generate-one-time-invoice/
GET    /api/v1/scholarships/  student-scholarships/                   CRUD; POST student-scholarships/{id}/end/
GET    /api/v1/invoices/                                              list/retrieve only (generated, not created)
POST   /api/v1/invoices/{id}/add-item/  cancel/  installments/
GET    /api/v1/invoices/me/  invoices/reports/student/  outstanding/  collection/
POST   /api/v1/invoices/assess-late-fees/
GET    /api/v1/payments/  receipts/  refunds/                         list/retrieve; POST payments/ records one
POST   /api/v1/payments/{id}/refund/

GET    /api/v1/event-categories/  events/                              CRUD (org-wide event needs an org-wide role)
POST   /api/v1/events/{id}/publish/  cancel/  register/
GET    /api/v1/events/{id}/registrations/  attendance/  participation/  roster/
POST   /api/v1/events/{id}/mark-attendance/  record-participation/
GET    /api/v1/events/me/
GET    /api/v1/event-registrations/                                   list/retrieve only (made via register/)
POST   /api/v1/event-registrations/{id}/decide/  withdraw/
GET    /api/v1/point-rules/  point-entries/                            CRUD; POST point-entries/ awards by hand
GET    /api/v1/student-points/  student-points/leaderboard/  student-points/me/
GET    /api/v1/awards/  award-rules/  student-awards/                  CRUD; POST student-awards/{id}/end/

GET    /api/v1/notifications/                                         list mine, across every org
POST   /api/v1/notifications/{id}/mark-read/  mark-all-read/
GET    /api/v1/notices/                                               CRUD; publish/read scoped by audience+campus
POST   /api/v1/notices/{id}/publish/
GET    /api/v1/communication/threads/                                 mine; POST starts (staff) or replies
GET    /api/v1/communication/threads/{id}/messages/  POST
POST   /api/v1/communication/threads/{id}/close/
GET    /api/v1/communication/appointment-slots/  appointments/         CRUD/book; read open to the organization
POST   /api/v1/communication/appointments/{id}/approve/  cancel/  complete/
GET    /api/v1/support/tickets/                                       CRUD (create only); POST {id}/assign/ etc.
GET    /api/v1/support/tickets/{id}/comments/  POST

GET    /api/v1/library/authors/  categories/  publishers/  books/      CRUD (office); read open to the organization
GET    /api/v1/library/shelves/  copies/                               CRUD (office); copies read open to all
POST   /api/v1/library/copies/{id}/withdraw/
GET    /api/v1/library/members/  members/me/                          CRUD (office); POST {id}/deactivate/
GET    /api/v1/library/issues/  issues/me/                             POST issues/ (desk); POST {id}/return/
GET    /api/v1/library/reservations/  reservations/me/                 POST reserves; {id}/cancel/  fulfil/
POST   /api/v1/library/reservations/expire-stale/
GET    /api/v1/library/fines/  fines/me/                               POST {id}/pay/  waive/

GET    /api/v1/inventory/categories/  suppliers/  items/  stores/      CRUD (office); DELETE 409 while in use
GET    /api/v1/inventory/stock-levels/  stock-movements/               ?low=1; POST stock-levels/adjust/
GET    /api/v1/inventory/stock-transfers/  stock-issues/               POST moves / issues stock (storekeeper)
GET    /api/v1/inventory/purchase-orders/                              POST drafts; {id}/place/  cancel/  receive/
GET    /api/v1/inventory/assets/  assets/me/                           POST registers; {id}/assign/  return/  move/  dispose/
GET    /api/v1/inventory/asset-assignments/  disposals/                read-only history
GET    /api/v1/inventory/maintenance/                                  POST schedules; {id}/start/  complete/  cancel/

GET    /api/v1/hr/positions/  fiscal-years/  leave-types/              CRUD (HR); leave types readable by all
GET    /api/v1/hr/contracts/  profiles/  documents/  (+ me/)           CRUD (HR); POST contracts/{id}/end/
GET    /api/v1/hr/leave-balances/  leave-balances/me/                  POST open/ ; POST {id}/adjust/
GET    /api/v1/hr/leave-requests/  pending/  me/                       POST me/ applies; {id}/approve/  reject/  cancel/

GET    /api/v1/payroll/settings/  components/  structures/  tax-schemes/   CRUD (accounts)
GET    /api/v1/payroll/staff-salaries/                                 POST assigns from a date
GET    /api/v1/payroll/runs/                                           POST; {id}/compute/  approve/  mark-paid/  cancel/  bank-sheet/
GET    /api/v1/payroll/payslips/  payslips/me/  adjustments/           POST payslips/{id}/set-overtime/

GET    /api/v1/hostel/buildings/  floors/  room-types/  rooms/  beds/   CRUD (warden); ?available=true
GET    /api/v1/hostel/allocations/  allocations/me/                    POST reserves; {id}/check-in/  check-out/  cancel/  move/
POST   /api/v1/hostel/allocations/generate-invoices/                   the term's hostel invoices (also needs finance.manage)
GET    /api/v1/hostel/complaints/  complaints/me/                      POST (office or me/); {id}/assign/  resolve/  reject/

GET    /api/v1/transport/vehicles/  vehicle-documents/  drivers/       CRUD; ?expiring_within= / ?license_expiring_within=
GET    /api/v1/transport/routes/  stops/  maintenance/  fuel-logs/     CRUD (transport office)
GET    /api/v1/transport/assignments/  assignments/me/                 POST puts a rider on a route; {id}/end/
POST   /api/v1/transport/assignments/generate-invoices/                the term's transport invoices (also needs finance.manage)
GET    /api/v1/transport/trips/  trips/mine/                           POST opens (crew); {id}/mark/  complete/
GET    /api/v1/transport/trip-records/  trip-records/me/               boarding history

GET    /api/v1/application-types/  application-types/available/        CRUD forms + approval steps; forms I can fill
GET    /api/v1/applications/  me/  pending/                            POST submits; {id}/approve/  reject/  send-back/  resubmit/  withdraw/
GET    /api/v1/certificates/  certificates/me/                         POST issues; {id}/revoke/
POST   /api/v1/public/organizations/{code}/applications/  status/  resubmit/  withdraw/   no login (admission)

GET    /api/v1/alumni/profiles/  me/  directory/  mentors/             CRUD (office); PATCH me/; POST graduate/
GET    /api/v1/alumni/employments/  higher-studies/  achievements/     the graduate's own, or the office
GET    /api/v1/alumni/events/  events/upcoming/                        CRUD; {id}/publish/  cancel/  rsvp/  rsvps/
GET    /api/v1/alumni/mentorships/                                     POST asks; {id}/accept/  decline/  end/
GET    /api/v1/alumni/campaigns/  campaigns/open/  donations/  donations/me/   POST donations/; {id}/refund/
GET    /api/v1/careers/vacancies/  vacancies/current/                  CRUD; {id}/open/  close/  apply/
GET    /api/v1/careers/candidacies/  interviews/  interviews/mine/  offers/  offers/mine/
POST   /api/v1/careers/candidacies/{id}/screen/   interviews/{id}/reschedule/  cancel/  outcome/   offers/{id}/withdraw/  respond/
GET    /api/v1/careers/postings/  postings/mine/                       the job board; POST posts; {id}/review/  close/
GET    /api/v1/public/organizations/{code}/careers/vacancies/          no login; POST vacancies/{id}/apply/  offer/  offer/respond/
POST   /api/v1/files/                      upload (multipart); GET files/  files/{id}/download/

GET    /api/v1/api-keys/                   CRUD (no delete); POST {id}/assign-role/  revoke-role/  rotate/  revoke/

GET    /api/v1/signup/config/  signup/check-code/                   no login; POST signup/  signup/resend/  signup/verify/
POST   /api/v1/auth/password-reset/  auth/password-reset/confirm/   no login
GET    /api/v1/signup-requests/            platform admins; POST {id}/approve/  reject/

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
├── core/              the identity foundation
│   ├── common/        abstract models, permissions, mixins, pagination, errors
│   ├── organizations/ Organization, Campus
│   ├── accounts/      User + user services
│   ├── authentication/ JWT login, refresh, logout, me, change-password
│   ├── permissions/   Permission, Role, UserRole + registry and selectors
│   ├── audit/         AuditLog, context middleware, audit services
│   ├── api_keys/      ApiKey: integration identities with roles, Api-Key auth
│   ├── files/         StoredFile: private uploads, type checks, access registry
│   └── signup/        public signup, email verification, password reset
├── modules/           business modules
│   ├── students/      Student, Enrollment
│   ├── parents/       Parent, StudentParent
│   ├── staff/         StaffMember
│   ├── admissions/    Admission
│   ├── academics/     Department, Program, Subject, curriculum, years, terms,
│   │                  Room, Batch, Section, TeachingAssignment
│   ├── timetable/     BellSchedule, Period, TimetableEntry, LessonChange,
│   │                  clash detection, generator
│   ├── attendance/    AttendanceSession, AttendanceRecord, StaffAttendanceDay,
│   │                  Punch, devices (QR, ZKTeco, generic)
│   ├── examinations/  GradeScale, Exam, ExamSubject, MarkSheet, Mark, Result,
│   │                  ResultPlan, ReportCard, Transcript
│   ├── finance/       FeeStructure, Invoice, Payment, Scholarship, Refund
│   ├── events/        Event, EventRegistration, PointRule, Award
│   ├── notifications/ Notification (the one door in — see integrations/)
│   ├── notices/       Notice
│   ├── communication/ MessageThread, Message, AppointmentSlot, Appointment
│   ├── support/       SupportTicket, TicketComment
│   ├── library/       Author, Category, Publisher, Book, Shelf, Copy, Member,
│   │                  Issue, Fine, Reservation
│   ├── inventory/     Item, Store, StockLevel, StockMovement, PurchaseOrder,
│   │                  Asset, AssetAssignment, MaintenanceRecord, Disposal
│   ├── hr/            Position, Contract, EmployeeProfile, StaffDocument,
│   │                  FiscalYear, LeaveType, LeaveBalance, LeaveRequest
│   ├── payroll/       PayComponent, SalaryStructure, StaffSalary, TaxScheme,
│   │                  PayrollRun, Payslip, PayrollAdjustment
│   ├── hostel/        Building, Floor, RoomType, HostelRoom, Bed, Allocation,
│   │                  Complaint
│   ├── transport/     Vehicle, VehicleDocument, Driver, Route, Stop, Assignment,
│   │                  Trip, TripRecord, Maintenance, FuelLog
│   ├── applications/  ApplicationType, ApprovalStep, Application,
│   │                  ApplicationEvent, Certificate
│   ├── alumni/        AlumniProfile, Employment, HigherStudy, Achievement,
│   │                  AlumniEvent, Rsvp, Mentorship, Campaign, Donation
│   └── careers/       Vacancy, Candidacy, Interview, JobOffer, JobPosting
├── integrations/
│   ├── biometric/     ZKTeco and generic device adapters
│   ├── email/  sms/  push/  one send() each; notifications.services.notify
│   │                  is the usual caller. Email can really send (EMAIL_DELIVERY)
│   └── captcha/       Turnstile, hCaptcha or reCAPTCHA check for public forms
└── tests/             shared factories, base test case, cross-cutting tests
```

Each app follows the same layout: `models` → `serializers` → `services`
(writes) / `selectors` (reads) → `views` → `urls` → `tests`.

### Five decisions worth knowing

**1. One identity, many profiles.** Students, parents, teachers and staff all
authenticate through a single `User`. The `Student`, `Parent` and
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
rejects any update. Financial corrections must be adjustments
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

## Detailed documentation

| Area | Document |
|---|---|
| Organizations, campuses, users, roles, permissions, audit log | [`docs/modules/identity.md`](docs/modules/identity.md) |
| Students, parents, staff, admissions | [`docs/modules/students.md`](docs/modules/students.md) |
| Programs, subjects, classes, calendar | [`docs/modules/academics.md`](docs/modules/academics.md), [`docs/modules/academics-real-life.md`](docs/modules/academics-real-life.md) |
| Timetable | [`docs/modules/timetable.md`](docs/modules/timetable.md) |
| Attendance and devices | [`docs/modules/attendance.md`](docs/modules/attendance.md) |
| Exams and results | [`docs/modules/examinations.md`](docs/modules/examinations.md) |
| Fees | [`docs/modules/finance.md`](docs/modules/finance.md) |
| Events and points | [`docs/modules/events.md`](docs/modules/events.md) |
| Notices, notifications, messages, support | [`docs/modules/communication.md`](docs/modules/communication.md) |
| Library | [`docs/modules/library.md`](docs/modules/library.md) |
| Inventory | [`docs/modules/inventory.md`](docs/modules/inventory.md) |
| HR and payroll | [`docs/modules/hr-payroll.md`](docs/modules/hr-payroll.md) |
| Hostel and transport | [`docs/modules/hostel-transport.md`](docs/modules/hostel-transport.md) |
| Applications and certificates | [`docs/modules/applications.md`](docs/modules/applications.md) |
| Alumni, careers, file uploads | [`docs/modules/alumni-careers.md`](docs/modules/alumni-careers.md) |
| API keys | [`docs/modules/api-keys.md`](docs/modules/api-keys.md) |
| Signup and password reset | [`docs/modules/signup.md`](docs/modules/signup.md) |
| Building a frontend | [`docs/frontend-brief.md`](docs/frontend-brief.md), [`docs/api/`](docs/api/) |

Not built yet: bulk import of existing paper or Excel records.
