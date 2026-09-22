# Education ERP — Backend

A modular education ERP platform (student information system, academics,
finance, HR and campus operations) built as a **modular monolith** on Django +
Django REST Framework.

**Status: Phase 1 complete.** The identity foundation everything else depends
on is built and tested. No business modules yet — see [Roadmap](#roadmap).

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
| 12 | Testing setup | `tests/`, 125 tests |
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
python manage.py test          # 125 tests, in-memory SQLite
```

`manage.py test` selects `config.settings.test` automatically.

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
