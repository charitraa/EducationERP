# Developer guide

How the backend is put together and how to change it without breaking
it. Read this before your first pull request.

- **What the product does:** [`README.md`](../README.md).
- **Each area in depth** (models, rules, decisions):
  [`docs/modules/`](modules/), one file per area.
- **Building a frontend:** [`frontend-brief.md`](frontend-brief.md) and
  [`api/`](api/).

Contents

1. [Getting set up](#1-getting-set-up)
2. [The map](#2-the-map)
3. [What happens on a request](#3-what-happens-on-a-request)
4. [The five rules everything follows](#4-the-five-rules-everything-follows)
5. [Inside a module](#5-inside-a-module)
6. [How modules talk to each other](#6-how-modules-talk-to-each-other)
7. [Recipe: a new module](#7-recipe-a-new-module)
8. [Recipe: a new endpoint or action](#8-recipe-a-new-endpoint-or-action)
9. [Tests](#9-tests)
10. [Settings, migrations and API docs](#10-settings-migrations-and-api-docs)
11. [Gotchas](#11-gotchas)
12. [Checklist before a pull request](#12-checklist-before-a-pull-request)

---

## 1. Getting set up

Follow [Quick start](../README.md#quick-start) in the README. The short
version, with SQLite so no database server is needed:

```bash
python3 -m venv .venv && source .venv/bin/activate
cd backend
pip install -r requirements.txt
cp .env.example .env              # set SECRET_KEY; DEV_DATABASE=sqlite
python manage.py migrate
python manage.py sync_permissions
python manage.py bootstrap_organization --name "Test College" --code test \
    --admin-email admin@test.edu --admin-password 'Admin-pass-12345'
python manage.py runserver        # http://127.0.0.1:8000/api/docs/
```

Everyday commands (from `backend/`):

| Command | What it does |
|---|---|
| `python manage.py test` | The whole suite (in-memory SQLite, test settings picked automatically) |
| `python manage.py test modules.library` | One module's tests |
| `python manage.py test tests.test_tenant_sweep` | The cross-tenant attack sweep (slow; run before a PR) |
| `python manage.py makemigrations <app>` | After a model change. Read the file it writes |
| `python manage.py sync_permissions` | After adding permissions or roles (idempotent) |
| `python manage.py check --deploy --settings=config.settings.production` | Production settings sanity check |

---

## 2. The map

```
backend/
├── config/              settings (base / development / production / test), URLs
│   └── api_v1.py        every module's urls.py is included here, under /api/v1/
├── core/                the platform: who you are and what you may do
│   ├── common/          base models, viewset mixins, permission classes,
│   │                    error envelope, pagination, shared serializer helpers
│   ├── organizations/   Organization (a school) and Campus (a branch)
│   ├── accounts/        User: the one login for everybody
│   ├── authentication/  login, refresh, logout, /auth/me/, 2FA
│   ├── permissions/     Permission, Role, UserRole; registry.py is the catalogue
│   ├── audit/           AuditLog (append-only) and log() helpers
│   ├── api_keys/        keys for other programs, with their own roles
│   ├── signup/          public signup, email verification, password reset
│   └── files/           private uploads and the "who may read it" registry
├── modules/             the business: students, academics, timetable,
│                        attendance, examinations, finance, events, notices,
│                        notifications, communication, support, library,
│                        inventory, hr, payroll, hostel, transport,
│                        applications, alumni, careers …
├── integrations/        the outside world, each behind one small function:
│                        email, sms, push, captcha, biometric devices
└── tests/               shared factories, the base test case, and the
                         cross-cutting tests (tenant sweep, schema, security)
```

`core` never imports from `modules`. A module may use `core` and any
module **listed above it** in `INSTALLED_APPS` (`config/settings/base.py`
is in dependency order). Going the other way (an earlier module reacting
to a later one) uses a signal or a registry; see
[section 6](#6-how-modules-talk-to-each-other).

---

## 3. What happens on a request

```
request
  │  RequestIDMiddleware          gives it an id, used in every log line
  │  security / CORS / CSRF …
  │  AuditContextMiddleware       remembers IP and user agent for the audit log
  ▼
DRF view
  │  authentication               JWT ("Bearer …") or API key ("Api-Key erp_…")
  │  throttles                    anon / user / per-scope / per-API-key
  │  permission_classes           HasPermission + IsSameOrganization (default)
  │  get_queryset()               ← narrowed to the caller's organization
  │                                 (and campus, for campus-owned records)
  │  serializer.is_valid()        shape + "does this id belong to my organization?"
  │  services.do_the_thing()      business rules, transaction, locks, audit, notify
  ▼
response                          or {"error": {"code", "message", "details"}}
```

Two things to take from this:

- **The tenant boundary is in the queryset, not in each view.** A view
  that inherits `OrganizationScopedViewSet` cannot forget it. A record from
  another organization is simply not found: **404, never 403**, so
  nobody learns it exists.
- **Business rules live in services**, so the same rule applies whether
  the call comes from the API, a management command, another module or a
  test.

---

## 4. The five rules everything follows

### 4.1 The organization always comes from the login

Every tenant table inherits `OrganizationOwnedModel`
(`core/common/models.py`), which adds `organization` plus timestamps and
soft delete. `OrganizationScopedMixin` (`core/common/mixins.py`):

- filters every queryset by `request.user.organization_id`, and
- sets `organization_id` on create from the user, **ignoring any
  `organization` in the body**.

The only exception is a platform superuser, who has no organization and
names one in the body.

**Foreign keys in a request body must be checked too.** A serializer that
accepts `{"campus": 7}` must make sure campus 7 is in the caller's
organization. Modules do this with a small helper:

```python
from core.common.serializers import target_organization_id

class OwnedSerializer(serializers.ModelSerializer):
    def own(self, value, label):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return value

class ShelfSerializer(OwnedSerializer):
    def validate_campus(self, value):
        return self.own(value, "campus")
```

The tenant sweep ([section 9](#9-tests)) attacks every endpoint with
another organization's ids, so a missing check fails the build.

### 4.2 Permissions are declared in code; views list what they need

Each module has a `permissions.py` that registers its codes:

```python
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions([
    PermissionSpec("library.manage", "Manage the library catalog and memberships"),
    PermissionSpec("library.circulate", "Issue, return, and settle fines and reservations"),
])
grant_to_system_role("campus-admin", ["library.manage", "library.circulate"])
```

The module's `apps.py` imports it in `ready()`, and
`manage.py sync_permissions` writes the catalogue to the database.
`org-admin` gets every permission automatically.

A view says what each action needs:

```python
required_permissions = {
    "create": ["library.manage"],
    "update": ["library.manage"],
    "destroy": ["library.manage"],
    "return_copy": ["library.circulate"],   # a custom @action, by method name
}
```

or, for plain CRUD, `permission_resource = "campuses"`, which maps to
`campuses.view` / `.create` / `.update` / `.delete`.

**An action with nothing declared is closed.** To make reads open to
everyone in the organization, override `get_permissions()`:

```python
def get_permissions(self):
    if self.action in ("list", "retrieve"):
        return [IsAuthenticated(), IsSameOrganization()]
    return super().get_permissions()
```

Never check `user_type` or role names in code. `user_type` is a label;
access comes only from roles.

### 4.3 Campus-scoped roles see only their campus

A role can be given for the whole organization or for one campus. For
records that belong to a campus, use `CampusScopedViewSet` (and
`campus_field` if the path is longer, e.g. `"copy__campus"`). It filters
the queryset to campuses where the caller holds the action's permission,
and refuses writes to other campuses.

Inside services, ask the same question with:

```python
from core.permissions.selectors import campus_ids_with_permission

campus_ids = campus_ids_with_permission(user, "library.circulate")
# None   -> held organization-wide (or superuser): every campus
# set()  -> nowhere
# {3, 5} -> only these campuses
```

`None` means everywhere. Don't write `if not campus_ids:`, because that treats
"everywhere" the same as "nowhere".

### 4.4 History is never rewritten

- **Soft delete** is the default (`BaseModel`): `DELETE` sets
  `deleted_at`. `Model.objects` hides deleted rows; `Model.all_objects`
  shows them. Unique constraints are **partial** (`condition=Q(deleted_at__isnull=True)`)
  so a deleted code can be reused.
- **Soft delete skips `on_delete=PROTECT`**, because no SQL delete happens. If
  something is still in use, refuse in `perform_destroy` with
  `ConflictError(..., code="in_use")`.
- **Money and marks are never edited.** A refund is a new row; a mark
  change is a `MarkCorrection`; an approved payslip is corrected by an
  adjustment on the next one. Financial models don't use soft delete.
- **Things that change over time take effect from a date**: placements,
  salaries, timetables, bell times. The past stays as it was, so old reports
  still give the same answer.
- **`AuditLog` is append-only.** It refuses updates.

### 4.5 Errors have one shape

Services raise these (from `core/common/exceptions.py`), never DRF
exceptions:

| Exception | HTTP | Use for |
|---|---|---|
| `ServiceError` | 400 | The request breaks a business rule |
| `ConflictError` | 409 | It clashes with the current state (taken, full, already done, in use) |
| `PermissionDeniedError` | 403 | A rule about *who* may do it that the view's permission can't express |

Always pass a **`code`** (`"not_available"`, `"timetable_clash"`); the
frontend switches on it. The `message` is shown to school staff as-is,
so write it in plain words. Put structured context in `details`.
Every error, including validation errors, reaches the client as:

```json
{"error": {"code": "not_available", "message": "This copy isn't available.", "details": null}}
```

---

## 5. Inside a module

Every module has the same files. `modules/library/` is a good one to read
first: it's small but uses every pattern.

| File | Holds | Rules |
|---|---|---|
| `models.py` | Tables | Inherit `OrganizationOwnedModel`. Explicit `db_table`, `ordering`, partial unique constraints. Status fields are `TextChoices`. No business logic beyond `__str__` and simple properties |
| `permissions.py` | Permission codes, grants to system roles | Codes are `module.action`. Imported from `apps.py` `ready()` |
| `serializers.py` | Input checking and output shape | Check every foreign id belongs to the organization. Use `ensure_unique_in_organization` for codes (MySQL can't enforce partial uniques). Separate input serializers for actions (`IssueBookSerializer`) |
| `services.py` | **All writes with rules** | Keyword-only args, `by=` for the acting user. `transaction.atomic()` + `select_for_update()` when two requests could race. Raise `ServiceError`s. Write the audit entry (`core.audit.services.log`). Call `notify()` |
| `selectors.py` | Reads other code needs | Query helpers other modules and views call instead of touching the models |
| `views.py` | HTTP only | Parse with a serializer → check access → call a service → serialize the result. Tag every operation with `extend_schema(tags=[TAG])` |
| `urls.py` | A `DefaultRouter` | Prefix routes with the module name (`library/books`) |
| `apps.py` | `AppConfig` | `label` = the module name; `ready()` imports `permissions` (and any receivers / registry hooks) |
| `admin.py` | Django admin | Optional |
| `tests/` | Tests | See [section 9](#9-tests) |

A typical service:

```python
def issue_book(*, copy: Copy, member: Member, by=None) -> Issue:
    if not member.is_active:
        raise ConflictError("This membership isn't active.", code="inactive_member")
    with transaction.atomic():
        copy = Copy.objects.select_for_update().get(pk=copy.pk)   # lock, then re-check
        if copy.status != CopyStatus.AVAILABLE:
            raise ConflictError("This copy isn't available.", code="not_available")
        issue = Issue.objects.create(organization_id=copy.organization_id, copy=copy,
                                     member=member, issued_by=by, ...)
        copy.status = CopyStatus.ISSUED
        copy.save(update_fields=["status", "updated_at"])
        log(AuditLog.Action.CREATE, instance=issue, module="library", actor=by)
    return issue
```

and the view that calls it:

```python
def create(self, request, *args, **kwargs):
    serializer = IssueBookSerializer(data=request.data, context=self.get_serializer_context())
    serializer.is_valid(raise_exception=True)
    services.ensure_can_circulate(request.user, serializer.validated_data["copy"].campus_id)
    issue = services.issue_book(by=request.user, **serializer.validated_data)
    return Response(IssueSerializer(issue).data, status=status.HTTP_201_CREATED)
```

**Auditing.** `OrganizationScopedViewSet` logs plain CRUD automatically
(`AuditedMixin`). When a service writes its own audit entry for a create,
set `service_audits_create = True` on the view so it isn't logged twice.

---

## 6. How modules talk to each other

Never query another module's tables from your module, and never write to
them. Use one of these:

| Need | Use | Example |
|---|---|---|
| Read or change another module's data | Its `selectors.py` / `services.py` functions | Payroll reads leave through `modules.hr.selectors`; approved leave is written to attendance through `modules.attendance.services` |
| Tell people something happened | `modules.notifications.services.notify(users, event_type=..., title=..., body=..., organization_id=...)` | Payment received, results published, a reserved book ready. Fans out to in-app, email, SMS and push |
| Reach someone with no account | `notify_address(email=..., phone=...)` | Public admission applicants |
| An earlier module announcing an event a later one reacts to | A Django signal in the earlier module, a receiver in the later one | `students.signals.student_graduated` → `alumni/receivers.py` creates the alumni profile |
| A new kind of request with approval steps | Add a `KindSpec` to `KINDS` in `modules/applications/kinds.py`: who it's for, its data serializer, the permission for the final step, and a `fulfil_*` function that calls your module's service | Hostel bed, bus seat, leave, job application |
| Attach files to your records | `core.files`: upload with a `purpose`, then `core.files.access.register("<module>.<thing>", check)` in `apps.py` `ready()`. Unregistered owner types are readable by nobody | `careers` registers who may read a résumé |
| Email, SMS, push, CAPTCHA, devices | `integrations/<name>/base.py`: one `send()` / `verify()` function | Swap the backend there; callers never change |

`notify()` and the integrations are the **only** way out of the system.
Don't call an email library from a module.

---

## 7. Recipe: a new module

Say you are adding `modules/clubs`.

1. **Create the app** with the files from [section 5](#5-inside-a-module).
   `apps.py`:

   ```python
   class ClubsConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "modules.clubs"
       label = "clubs"
       verbose_name = "Clubs"

       def ready(self):
           from . import permissions  # noqa: F401
   ```

2. **Register it**: add `"modules.clubs"` to `INSTALLED_APPS` *after*
   every module it uses, and `path("", include("modules.clubs.urls"))` to
   `config/api_v1.py`.

3. **Models** inherit `OrganizationOwnedModel`:

   ```python
   ALIVE = Q(deleted_at__isnull=True)

   class Club(OrganizationOwnedModel):
       campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="clubs")
       code = models.CharField(max_length=30)
       name = models.CharField(max_length=100)

       class Meta:
           db_table = "clubs_club"
           ordering = ["campus_id", "code"]
           constraints = [
               models.UniqueConstraint(fields=["organization", "campus", "code"], condition=ALIVE,
                                       name="uniq_clubs_club_code"),
           ]
   ```

   Then `python manage.py makemigrations clubs` and read the migration.

4. **Permissions** in `permissions.py`; grant to `campus-admin`, `staff`
   etc. only what those roles should obviously have. Run
   `sync_permissions`.

5. **Serializers**: check every foreign id (section 4.1), and use
   `ensure_unique_in_organization` for codes.

6. **Services** for anything beyond plain CRUD.

7. **Views**: `CampusScopedViewSet` for campus-owned records,
   `OrganizationScopedViewSet` for organization-wide ones. Declare
   `required_permissions`, `audit_module`, `filterset_fields`,
   `search_fields`, and schema tags.

8. **Tests** in `modules/clubs/tests/`, then run the **tenant sweep**. It
   will fail and name what to add: a victim row for each new model in
   `build_tenant()` in `tests/test_tenant_sweep.py`, and an entry in
   `ACTION_ATTACKS` for custom actions that take ids in their body. That's
   on purpose; see [section 9](#9-tests).

9. **Docs**: a `docs/modules/<module>.md` in the style of the others, a line in the
   README's *What it does* and *Detailed documentation*, and regenerate
   `docs/api/` ([section 10](#10-settings-migrations-and-api-docs)).

---

## 8. Recipe: a new endpoint or action

```python
@extend_schema(tags=[TAG], summary="Close the club", request=CloseClubSerializer,
               responses={200: ClubSerializer})
@action(detail=True, methods=["post"])
def close(self, request, pk=None):
    club = self.get_object()                       # already tenant- and campus-scoped
    serializer = CloseClubSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    club = services.close_club(club, by=request.user, **serializer.validated_data)
    return Response(ClubSerializer(club).data)
```

- Add `"close": ["clubs.manage"]` to `required_permissions`. Without it,
  the action is closed to everyone except superusers.
- Always fetch the target with `self.get_object()`, never
  `Club.objects.get(pk=pk)`, which skips the tenant filter.
- Describe the request body with `extend_schema(request=...)`. If the view's
  `serializer_class` doesn't match what `create()` really reads, add it to
  `TAKES_A_BODY` in `tests/test_schema.py`.
- "Me" endpoints (`/library/issues/me/`) find the caller's own records
  from `request.user`, never from an id in the request.
- If the action takes ids of other records in its body, the sweep's guard
  test will ask you to add it to `ACTION_ATTACKS`.

---

## 9. Tests

```python
from tests.base import APITestCaseBase
from tests.factories import create_campus, create_organization, user_with_system_role

class ClubTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization()
        self.campus = create_campus(self.org)
        self.admin = user_with_system_role(self.org, "org-admin")
        self.authenticate(self.admin)          # logs in through the real endpoint

    def test_create(self):
        r = self.client.post("/api/v1/clubs/", {"campus": self.campus.pk, "code": "chess", "name": "Chess"})
        self.assertEqual(r.status_code, 201, r.data)
```

- `APITestCaseBase` syncs the permission catalogue once per class.
- `tests/factories.py` has a `create_*` for every common record (students,
  sections, terms, timetable entries, devices…) and `user_with_permissions`
  / `user_with_system_role` / `grant` for access. Add new factories there.
- Test the **refusals** as much as the happy path: no permission (403),
  another campus (404 or 403), another organization (404), the conflict
  codes your service raises, and races where it matters.
- Call services directly in tests for rules; call the API for access.

Cross-cutting tests in `backend/tests/` guard the whole codebase:

| Test | What it catches |
|---|---|
| `test_tenant_sweep.py` | Builds two organizations with one row of **every** tenant model, then attacks every route from one against the other: foreign ids in URLs, filters and bodies. Fails if anything leaks, changes or links across. Its **guard tests** fail when a new model, view or action isn't covered yet, and say what to add (`build_tenant()`, `ACTION_ATTACKS`, `SELF_ONLY_VIEWS`) |
| `test_tenant_isolation.py` | Hand-written isolation cases |
| `test_schema.py` | The OpenAPI schema builds **with no warnings**, and request bodies are documented |
| `test_env_example.py` | Every `config("X")` in settings is documented in `.env.example`, and nothing extra |
| `test_security.py`, `test_cors.py` | Headers, CSP, CORS, production settings |

A new public view (no login) belongs in `SELF_ONLY_VIEWS` only with a
comment saying how it is tenant-safe and where that is tested.

---

## 10. Settings, migrations and API docs

**Settings** are split: `base.py` (everything), `development.py`,
`production.py`, `test.py`. Read env vars with `config("NAME", default=...)`
and add every new one to `backend/.env.example` with a comment; the test
above enforces it. Production refuses to start with unsafe combinations
(e.g. signup on without a CAPTCHA), so add such checks to `production.py`
when you add a feature that needs them.

**Migrations**: one per change, reviewed by hand. Never edit a migration
that may already be applied somewhere; add a new one. Partial unique
constraints are fine: MySQL ignores them (Django warns `models.W036`, which
development settings silence), which is why serializers check uniqueness
too.

**API docs** in `docs/api/` are generated. After any API change, from
`backend/` with the **default development settings** (test settings give a
different security scheme):

```bash
python manage.py spectacular --file ../docs/api/openapi.yaml
python ../docs/api/make_index.py      # rebuilds endpoints.md
```

The output order can change from run to run, so expect a noisy diff.

---

## 11. Gotchas

- **Soft delete bypasses `PROTECT`.** Check "in use" yourself (4.4).
- **`campus_ids_with_permission` returns `None` for "everywhere"** (4.3).
- **Never trust ids from the body**: check the organization in the
  serializer (4.1), and use `get_object()` for the URL id.
- **Lock, then re-check.** Read the row with `select_for_update()` inside
  `transaction.atomic()` and check its state *after* locking, or two
  requests can both take the last bed.
- **Idempotent bulk actions.** Generating invoices, admit cards or
  attendance sessions twice must not create doubles; offline clients
  resend with a `client_key`.
- **Numbering** (receipts, accession numbers) counts `all_objects`,
  including deleted rows, under a lock on the organization row.
- **Enum names in the schema**: when two serializers expose a field with
  the same name but different choices, `drf-spectacular` warns and the
  schema test fails. Pin a name in `ENUM_NAME_OVERRIDES`
  (`config/settings/base.py`), and keep existing names stable; clients are
  generated from them.
- **Don't name a test helper `run`**: it shadows `unittest.TestCase.run`
  and the test silently does nothing.
- **New user types** fall back to the staff notice audience. Check notices
  if you add one.
- **Email in development** is logged to the console
  (`EMAIL_DELIVERY=console`). Set `EMAIL_DELIVERY=django` and Django's
  `EMAIL_*` settings to really send.
- **Dates** are stored in AD. Bikram Sambat appears only in free-text names
  (`2082/83`) and in date ranges the user picks (payroll periods).
- **Nothing country-specific is hardcoded.** Tax slabs, grade scales,
  fee structures and leave quotas are data.

---

## 12. Checklist before a pull request

- [ ] Models inherit `OrganizationOwnedModel`; uniques are partial; the migration is read.
- [ ] Every foreign id in every serializer is checked against the organization.
- [ ] Every action has `required_permissions` (or a deliberate `get_permissions()`).
- [ ] Rules live in `services.py`, raise `ServiceError`s with a `code`, and write the audit log.
- [ ] Concurrency: lock-then-check wherever two requests could collide.
- [ ] No reaching into another module's tables; `notify()` for messages.
- [ ] Tests for the refusals, not only the happy path.
- [ ] `python manage.py test` passes, **including the tenant sweep and the schema test**.
- [ ] New env vars are in `.env.example`.
- [ ] `docs/api/` regenerated; module docs and README updated.
