# API keys

Programs use the API with a key instead of a person's login: a school's
website posting admission forms, an SMS gateway, a reporting tool, a
mobile app's back office. This is the first step of running the system
as a free, self-signup service (see the README roadmap). It lives in
`backend/core/api_keys/`.

```
ApiKey (organization, name, prefix, sha256(secret), read_only, expires_at, allowed_ips, rate_limit, revoked_at)
   └── user ──► User (user_type "integration": no password, never listed)
                  └── UserRole ──► Role (optionally at one campus)   ← the key's access
```

## Decisions (agreed before building)

| Question | Choice |
|---|---|
| What a key acts as | **Its own identity with roles.** Each key is a non-human integration with roles, optionally per campus, exactly as a person has. It survives the person who made it leaving. |
| Narrowing | **Roles, plus a read-only switch** that refuses every write: for reporting tools. |
| Lifetime and limits | **Optional expiry, an optional address allowlist (IPs or CIDR networks), its own rate limit**, last-used tracking, revoke and rotate. The secret is shown once and stored hashed. |

## How it works

- **Using a key**: `Authorization: Api-Key erp_<prefix>_<secret>`, or
  the `X-API-Key` header. JWT logins work as before; a request uses one or
  the other.
- **The key's identity is a user.** Each key is backed by a user with
  `user_type="integration"`, which can't log in (no usable password) and
  is excluded from `/users/`, so it can't be edited, reset or given roles
  there. Roles go to it through `/api-keys/{id}/assign-role/`. Every
  existing check then applies unchanged: permissions, campus scoping,
  tenant isolation and audit. The audit log's actor is the key, named
  after it. Notifications never go to keys (`users_holding` skips them).
- **No escalation.** Granting a role to a key obeys the same rule as
  granting it to a person: you can't grant permissions you don't hold
  yourself, at that scope. A create whose roles are refused creates
  nothing. Keys can't manage keys, even with `api_keys.manage` among
  their roles.
- **Refusals.** An unknown, wrong, revoked or expired key, or one whose
  organization is inactive, gets `401` with the same message, so a probe
  learns nothing. A valid key used from an address outside its allowlist
  gets `403 ip_not_allowed`. A write with a read-only key gets
  `403 read_only_key`. The client address follows `NUM_PROXIES`, as
  for the rate limiter and the audit log.
- **The secret.** It's 256 random bits. Only its SHA-256 is stored (a
  slow hash adds nothing at that entropy) and it's compared in constant
  time. It's returned once, by `create` and `rotate`. `erp_` makes a
  leaked key easy for secret scanners to spot; the public prefix finds the
  row.
- **Rate limit.** A key's `rate_limit` (e.g. `1000/hour`), or
  `THROTTLE_API_KEY` (300/min) when it has none. It's counted per key, on
  top of the general per-user ceiling.
- **Last used.** The time and address are written at most once a minute,
  or when the address changes.

## Endpoints (`api_keys.manage`)

| | |
|---|---|
| `GET /api-keys/` | List keys: name, prefix, roles, whether usable, last used, revoked |
| `POST /api-keys/` | Create, with `grants: [{role, campus?}]`. The response carries `key` once |
| `PATCH /api-keys/{id}/` | Rename, read-only, expiry, `allowed_ips`, `rate_limit` |
| `POST /api-keys/{id}/assign-role/`, `revoke-role/` | Change its roles |
| `POST /api-keys/{id}/rotate/` | A new secret (and prefix); the old one stops at once |
| `POST /api-keys/{id}/revoke/` | It stops working at once and stays on record |

Keys are never deleted. Only `org-admin` holds `api_keys.manage` among
the shipped roles: a key belongs to the organization. An organization can
give the permission to its own role.

## Changes outside the app

- `User.Type.INTEGRATION` (migration `accounts.0005_integration_user_type`).
- `UserViewSet` leaves integration users out. `users_holding()` (who to
  notify) leaves them out.
- `DEFAULT_AUTHENTICATION_CLASSES` adds `ApiKeyAuthentication` after JWT.
  `DEFAULT_THROTTLE_CLASSES` adds `ApiKeyRateThrottle`. A new rate,
  `THROTTLE_API_KEY`.
- OpenAPI documents the `ApiKey` security scheme next to JWT.
- The tenant sweep has a key per organization and attacks the role
  grants with the other organization's role and campus.

Found on the way, and fixed: lists that annotate a count (classes,
inventory items, vacancies, mentors) had **no fixed order**. Django drops
`Meta.ordering` from a `GROUP BY` query, so paging could repeat or skip
rows. Those querysets now order explicitly. `tests/test_ordering.py`
checks every list endpoint's queryset is ordered.

## Testing

13 tests (`core/api_keys/tests`):

- a key acting with its roles and only at its campus; both headers;
  writes audited as the key
- read-only keys; unknown, wrong, expired and revoked keys
- rotation; the address allowlist; per-key rate limits
- other organizations out of reach
- integration users hidden from the user API, unable to log in, never
  notified
- no granting beyond your own permissions, and nothing half-made when
  refused
- keys can't manage keys; campus admins can't manage keys
- foreign roles and campuses refused

## Not built (by choice, for now)

- **Personal access tokens** (a key acting as a person). Not chosen:
  they break when the person leaves and can't be narrowed.
- **Per-key permission lists** independent of roles. Roles plus
  read-only was chosen.
- **Usage statistics per key** beyond last used. Quotas and metering come
  with step 3 (per-organization throttles, quotas, module toggles).
