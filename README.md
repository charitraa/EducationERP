# Education ERP — Backend

A modular education ERP platform (student information system, academics,
finance, HR and campus operations) built as a **modular monolith** on Django +
Django REST Framework.

**Status: Phase 1 complete.** The identity foundation everything else depends
on is built and tested. No business modules yet — see [Roadmap](#roadmap).

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
| 12 | Testing setup | `tests/`, 143 tests |
| 13 | API documentation | OpenAPI 3 at `/api/docs/` |

---

## Quick start

```bash
cd backend
source ../.venv/bin/activate          # or: python3 -m venv .venv && pip install -r requirements.txt

cp .env.example .env                  # then set SECRET_KEY
python manage.py migrate
python manage.py sync_permissions     # load the permission catalogue + system roles

python manage.py bootstrap_organization \
    --name "Central College" --code central-college \
    --admin-email admin@central.edu --admin-password 'Admin-pass-12345'

python manage.py runserver
```

Then open <http://127.0.0.1:8000/api/docs/>.

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
python manage.py test          # 143 tests, in-memory SQLite
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
| `make dev` | Dev stack in the foreground (`DEV_USE_POSTGRES=True`, `DEBUG=True`) |
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
  `campus-admin` (manage users and campuses), `staff` (read-only basics),
  `student` and `parent` (empty until later phases add to them). Each
  organization can also create its own roles.
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
| Ram, Lalitpur head | `campus-admin` | Lalitpur Campus | users and campus settings (see [Known gaps](#known-gaps)) |
| Accountant | `staff` | whole organization | read-only basics |
| A student | `student` | their campus | nothing yet: Phase 2+ adds student features |

A role given **without** a campus counts everywhere in the organization. A role
given **with** a campus is meant to count only there.

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
`POST /auth/logout/` blocks the refresh token. Logins are limited to 10 tries a
minute, and every login, failed login and logout is written to the audit log.

### Known gaps

Fix these before a real multi-branch institution goes live:

- **Campus roles are not limited to their campus yet.** A `campus-admin` for
  Lalitpur can currently also manage Baneshwor and Bhaktapur. The permission
  check does not know which campus a request is about, so it counts every role
  the user holds. Single-campus schools are not affected.
- **More than one campus can be marked as main.** Keep `is_main` on only one.
- **`bootstrap_organization --type` is not validated**, so a typo such as
  `--type skool` is saved as-is.

---

## API surface (v1)

```
POST   /api/v1/auth/login/                 email + password -> token pair + profile
POST   /api/v1/auth/refresh/               refresh -> new access token
POST   /api/v1/auth/logout/                blacklist a refresh token
GET    /api/v1/auth/me/                    current user, roles, permissions
PATCH  /api/v1/auth/me/                    update own contact details
POST   /api/v1/auth/change-password/

GET    /api/v1/organizations/              CRUD (tenant-scoped)
GET    /api/v1/campuses/                   CRUD (tenant-scoped)

GET    /api/v1/users/                      CRUD (tenant-scoped)
GET    /api/v1/users/{id}/roles/
POST   /api/v1/users/{id}/assign-role/
POST   /api/v1/users/{id}/revoke-role/
POST   /api/v1/users/{id}/set-password/
POST   /api/v1/users/{id}/deactivate/

GET    /api/v1/permissions/                read-only catalogue
GET    /api/v1/roles/                      CRUD (system roles are read-only)

GET    /api/v1/audit-logs/                 read-only trail

GET    /health/                            liveness
GET    /ready/                             readiness (checks the database)
GET    /api/schema/  /api/docs/  /api/redoc/
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
├── modules/           Phase 2+ business modules (empty)
├── integrations/      biometric, payment, SMS, email, push (empty)
└── tests/             shared factories, base test case, cross-cutting tests
```

Each app follows the same layout: `models` → `serializers` → `services`
(writes) / `selectors` (reads) → `views` → `urls` → `tests`.

### Five decisions worth knowing

**1. One identity, many profiles.** Students, parents, teachers and staff all
authenticate through a single `User`. Phase 2 adds `StudentProfile`,
`StaffProfile` and so on, each pointing back at one `User` — never a second
login system. `user_type` is descriptive; access always comes from roles.

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

Phase 1 is done. Next, in order:

| Phase | Scope |
|-------|-------|
| **2** | Students, Parents, Staff, Admissions, Enrollment, academic structure |
| 3 | Courses, Subjects, Timetable, Attendance engine, QR/biometric |
| 4 | Exams, Grades, Transcripts, Report cards |
| 5 | Fees, Invoices, Payments, Scholarships |
| 6 | Events, Registration, Points, Achievements |
| 7 | Library, Inventory, Transport, Hostel |
| 8 | HR, Leave, Payroll |
| 9 | Support, Communication, Notices, Alumni, Careers, Applications |
| 10 | Analytics, reporting, advanced integrations |

Deferred until a real requirement justifies them: Redis, Celery, WebSockets,
object storage, Docker, search.

See [`docs/phase-1.md`](docs/phase-1.md) for the data model and the conventions
Phase 2 should follow.
