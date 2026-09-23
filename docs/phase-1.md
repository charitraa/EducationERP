# Phase 1 — Identity Foundation

Reference for the models, rules and conventions established in Phase 1.
Phase 2 modules should follow these rather than inventing new patterns.

---

## Data model

```
Organization  (tenant)
   │
   ├── Campus                     code unique per organization
   │
   ├── User                       email unique platform-wide
   │      │
   │      └── UserRole ──────► Role ──────► Permission
   │            │                 │              ▲
   │            └── Campus        │              │
   │               (optional      └── system roles have organization = NULL
   │                scope)
   │
   └── AuditLog                   append-only
```

### Organization
Tenant root. Everything tenant-owned points here. `code` is a lowercase slug,
unique platform-wide and immutable through the API once set.

### Campus
A location inside an organization. `code` is unique per organization, enforced
by a partial unique constraint that ignores soft-deleted rows. Roles can be
scoped to a campus.

### User
The single identity for every human on the platform. Email is the username and
is unique platform-wide — a person needing access to two institutions gets two
accounts, which keeps login unambiguous.

- `organization` is null **only** for platform superusers.
- `is_platform_admin` is `is_superuser AND organization is None`. A superuser
  bound to a tenant deliberately does *not* get cross-tenant reach.
- `user_type` is descriptive (student / parent / teacher / …). It never grants
  access; roles do.

### Permission
One grantable capability, coded `module.action` (`students.view`,
`attendance.mark`). Declared in `core/permissions/registry.py`, synced by
`manage.py sync_permissions`. The API exposes the catalogue read-only.

### Role
A bundle of permissions. `organization = NULL` + `is_system = True` means a
platform-shipped role that no tenant can edit or delete. Organizations define
their own roles alongside them.

Phase 1 system roles: `org-admin` (all permissions, re-expanded on every sync
so new modules are included automatically), `campus-admin`, `staff`,
`student`, `parent`.

### UserRole
Assignment of a role to a user, optionally scoped to one campus
(`campus = NULL` means organization-wide), with optional `expires_at` for
temporary access. `clean()` refuses a role or campus from another tenant.

### AuditLog
Append-only. Records actor (plus `actor_email`, captured at write time so the
trail survives user deletion), organization, action, module, object type/id/
repr, a `{field: {before, after}}` diff, IP, user agent and request path.

---

## Permission catalogue (Phase 1)

```
organizations.view    organizations.create    organizations.update    organizations.delete
campuses.view         campuses.create         campuses.update         campuses.delete
users.view            users.create            users.update            users.delete
users.manage_roles
roles.view            roles.create            roles.update            roles.delete
permissions.view
audit.view
```

---

## Conventions for Phase 2 and beyond

### Adding a module

1. Create `modules/<name>/` with `models, serializers, services, selectors,
   views, urls, permissions, tests/, migrations/`.
2. Add an `AppConfig` with an explicit `label`, and register it in
   `MODULE_APPS` in `config/settings/base.py`.
3. Declare permissions in `core/permissions/registry.py` (or call
   `register_permissions` from the app's `AppConfig.ready`), then run
   `manage.py sync_permissions`.
4. Add the route to `config/api_v1.py`.
5. Write tests before moving on.

### Model rules

- Tenant-owned models subclass `OrganizationOwnedModel` (or `BaseModel` plus an
  explicit `organization` FK).
- Use foreign keys, never denormalised copies — no `student_name` columns.
- Unique constraints on soft-deletable models need
  `condition=Q(deleted_at__isnull=True)`.
- Financial and audit records must **not** use soft delete. Correct them with
  adjustments or reversals.
- Nothing backend-specific. Dev is SQLite, production is PostgreSQL; both must
  behave the same.

### View rules

- Subclass `OrganizationScopedViewSet` for tenant-owned resources. It gives
  tenant scoping, audit logging, soft delete and permission enforcement.
- Declare `required_permissions` per action. An undeclared action is denied.
- Never trust `organization`, `student_id`, `role` or amounts from a payload —
  derive them from the authenticated user, or validate against a queryset that
  is already tenant-scoped.

### Cross-module rules

Modules call each other's `services.py` / `selectors.py`. A module must never
query or write another module's tables directly — that boundary is what makes
a later service extraction possible.

---

## Test coverage (125 tests)

| Area | What is covered |
|------|-----------------|
| Models | uniqueness, validators, soft delete, restore, cascades, timestamps |
| Auth | login, case-insensitive email, wrong password, inactive and soft-deleted users, identical error for unknown email, refresh, logout blacklisting, throttling |
| RBAC | role resolution, multi-role merge, campus scoping, expiry, deleted roles, inactive users, superuser bypass, cache invalidation |
| Tenant isolation | list/retrieve/update/delete across tenants, payload tenant spoofing, cross-tenant password reset and role assignment, audit visibility, users with no organization |
| Permissions | anonymous denied, no-permission denied, view ≠ create, update ≠ delete, role management gated separately, revocation takes effect |
| Audit | actor/IP/request capture, before-after diffs, no entry for no-op updates, immutability, never raising on failure |
| API contract | error envelope shape, pagination, search, filtering, versioning, OpenAPI schema generation, health and readiness |

---

## Known gaps (deliberate)

Not in Phase 1; add when the need is real:

- **2FA and device/session management** — the spec lists these under long-term
  security. JWT + refresh rotation + blacklisting is the Phase 1 baseline.
- **Password reset by email** — needs a mail transport decision. Administrative
  reset via `POST /users/{id}/set-password/` covers the gap.
- **File storage (`core/files/`)** — not required until modules have documents
  to store (Phase 2+). Belongs in object storage, not the database.
- **Rate limiting beyond login** — only the login endpoint is throttled.
- **Redis, Celery, WebSockets** — deferred by design. Docker packaging was
  added afterwards for onboarding and deployment; see the README.
