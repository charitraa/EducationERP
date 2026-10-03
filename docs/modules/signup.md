# Public signup, email verification, CAPTCHA, password reset

Step 2 of running the platform as a free, self-signup service (see the
README roadmap). Anyone can create their own organization from a public
form. It lives in `backend/core/signup/`, with the CAPTCHA adapter in
`backend/integrations/captcha/`.

```
POST /signup/  ──► SignupRequest (pending, password hashed, sha256(token))
                     │  email: link with the token
POST /signup/verify/ ┤
                     ├─ approval off ──► Organization + main Campus + org-admin User, signed in (201)
                     └─ approval on  ──► awaiting_approval ──► platform admin approves ──► same, emailed
```

## Decisions (agreed before building)

| Question | Choice |
|---|---|
| CAPTCHA | **A pluggable adapter.** Cloudflare Turnstile is the default; hCaptcha and reCAPTCHA (v2 or v3 with a minimum score) are alternatives; `off` for development and tests. |
| When the organization is created | **Only after the email is verified.** A signup is just a request until its link is opened. Unverified signups leave no junk organizations and reserve nothing. |
| How the email is verified | **A link with a single-use token** that expires (24 h by default). It can be sent again, which replaces the old link. |
| Extras | **Forgot/reset password**, **blocking throwaway email domains**, a **platform-approval switch** (off by default) and a **public code-availability check**. |

## How it works

- **Off unless switched on.** `SIGNUP_ENABLED=False` by default: a
  school running its own copy doesn't want strangers creating
  organizations on it. While it's off, every signup endpoint except
  `config` answers `403 signup_disabled`. Password reset works either way.
- **Production checks.** With signup on, production refuses to start
  unless a CAPTCHA is configured (`CAPTCHA_PROVIDER` and
  `CAPTCHA_SECRET_KEY`) and `EMAIL_DELIVERY=django` (verification links
  have to really arrive).
- **Nothing is created until the link is opened.** `POST /signup/`
  validates the form, checks the CAPTCHA, stores a `SignupRequest` with
  the password already hashed (the usual Django hasher) and only the
  SHA-256 of a 256-bit token, then emails the link. Opening it
  (`POST /signup/verify/` with the token) creates the organization, a main
  campus ("Main Campus", code `main`) and the person as its `org-admin`,
  using the same `create_organization` service as
  `manage.py bootstrap_organization`. The response carries a token pair,
  so the person is signed in straight away. The request is kept as
  `completed`, with the password hash removed.
- **Codes and emails aren't reserved by unverified signups.** Otherwise
  anyone could squat on a code by signing up and never verifying. If
  someone else verifies first, the slower link gets `409 code_taken` (or
  `email_taken`). A verified signup waiting for approval does hold its code
  and email.
- **Nothing is revealed about who has an account.** Signing up with an
  email that already has an account gets the same `202` answer, and that
  address is sent a note ("you already have an account; sign in or reset
  your password") instead of a link. Resend and forgot-password answer
  `202` whether or not there was anything to send.
- **A second signup from the same email replaces the first**, so only
  the newest link works. Resending issues a new token and the old one
  stops working.
- **Mail flooding.** Besides the per-address rate limits on the
  endpoints, one email address receives at most
  `EMAILS_PER_ADDRESS_PER_HOUR` (5) mails from signup, resend and reset.
  Past that, mail is quietly not sent, so the answer stays the same.
- **Throwaway email domains** (mailinator, yopmail, guerrillamail and
  about 100 more, plus their subdomains) are refused with a field error.
  Add more with `SIGNUP_BLOCKED_EMAIL_DOMAINS`. The list can't be
  complete. It raises the cost of junk signups; the CAPTCHA and the link
  do the real work.
- **Reserved codes**: `admin`, `api`, `www`, `app`, `login`, `signup`,
  `support` and similar. They would look official or clash with
  subdomains (`code.example.com`). `bootstrap_organization` can still use
  them.
- **The password** is checked by the same validators as every other
  password, including similarity to the person's own name and email.
- **Platform approval.** With `SIGNUP_REQUIRE_APPROVAL=True`, opening the
  link moves the request to `awaiting_approval` and emails every platform
  admin. A platform admin approves it (the organization is created and
  its admin is emailed) or rejects it with a reason (the person is
  emailed the reason, and the code becomes free again).
- **Cleanup.** `manage.py purge_signup_requests [--days 7]` deletes
  unverified requests whose link expired that many days ago. Run it daily
  from cron.

## CAPTCHA

`integrations/captcha/base.py`: `verify(token, ip)` and a view helper,
`require(request, token)`, which answers `400 captcha_failed`. Turnstile,
hCaptcha and reCAPTCHA share the same "siteverify" protocol, so one
function covers all three. It **fails closed**: if the provider can't be
reached, the check fails. Otherwise an outage would switch the protection
off. It's checked on `POST /signup/`, `POST /signup/resend/` and the
the **public application form** (`captcha_token` in the body), which
had been waiting for this step. The frontend reads
`GET /signup/config/` to know which widget to show, and with which site
key.

## Password reset

Django's own reset tokens. A token is tied to the account's current
password and last login, so it stops working once it has been used, and
it expires after `PASSWORD_RESET_MINUTES` (60). The link carries
`uid` and `token`. Confirming:

- checks the new password with the usual validators,
- **signs the account out everywhere** (every outstanding refresh token
  is blacklisted: whoever knew the old password may hold one),
- clears an account lockout, and
- is written to the audit log as a password change.

No mail goes to inactive users, API-key users, or users of an inactive
organization. The answer is the same either way.

## Email delivery

`integrations/email` used to only log mail. `EMAIL_DELIVERY=django`
now sends it through Django's mail settings (`EMAIL_HOST` and the rest,
or any provider with a Django email backend), from `DEFAULT_FROM_EMAIL`.
A mail server that's down doesn't fail the request: the error is logged,
and the person can ask again. This switch applies to all notification
email, not just signup.

## Endpoints

Public (no login; a stray `Authorization` header is ignored):

| | |
|---|---|
| `GET /signup/config/` | `enabled`, `requires_approval`, `captcha_provider`, `captcha_site_key`, `organization_types` |
| `GET /signup/check-code/?code=` | `{code, available, reason}`; reason is `invalid`, `reserved` or `taken` |
| `POST /signup/` | `organization_name`, `organization_code`, `organization_type`, `timezone`, `first_name`, `last_name`, `email`, `phone`, `password`, `captcha_token` → `202` |
| `POST /signup/resend/` | `email`, `captcha_token` → `202` |
| `POST /signup/verify/` | `token` → `201` with `organization`, `access`, `refresh`, `user`; or `202 {status: "awaiting_approval"}` |
| `POST /auth/password-reset/` | `email` → `202` |
| `POST /auth/password-reset/confirm/` | `uid`, `token`, `new_password` → `204` |

Platform admins only (a superuser with no organization):

| | |
|---|---|
| `GET /signup-requests/` | Every signup; filter `status`, `organization_type`; search name, code, email. Never shows password or token hashes |
| `POST /signup-requests/{id}/approve/` | Create the organization and email its admin |
| `POST /signup-requests/{id}/reject/` | `reason`; the person is emailed it |

Error codes: `signup_disabled`, `captcha_failed`, `invalid_token`,
`expired_token`, `code_taken`, `email_taken`, `not_awaiting_approval`.

## Settings

| Setting | Default | |
|---|---|---|
| `SIGNUP_ENABLED` | `False` | |
| `SIGNUP_REQUIRE_APPROVAL` | `False` | |
| `SIGNUP_TOKEN_HOURS` | `24` | How long the verification link works |
| `SIGNUP_VERIFY_URL` | `http://localhost:5173/signup/verify?token={token}` | The frontend page the link opens |
| `PASSWORD_RESET_URL` | `http://localhost:5173/reset-password?uid={uid}&token={token}` | |
| `PASSWORD_RESET_MINUTES` | `60` | |
| `SIGNUP_BLOCKED_EMAIL_DOMAINS` | — | Comma-separated, on top of the built-in list |
| `EMAILS_PER_ADDRESS_PER_HOUR` | `5` | |
| `CAPTCHA_PROVIDER` | `off` | `off`, `turnstile`, `hcaptcha`, `recaptcha` |
| `CAPTCHA_SITE_KEY`, `CAPTCHA_SECRET_KEY` | — | |
| `CAPTCHA_MIN_SCORE` | `0.5` | reCAPTCHA v3 only |
| `EMAIL_DELIVERY` | `console` | `django` sends real mail |
| `DEFAULT_FROM_EMAIL` | `Education ERP <no-reply@localhost>` | |
| `THROTTLE_SIGNUP` | `10/hour` | Signup and resend, per address |
| `THROTTLE_SIGNUP_CHECK` | `60/min` | Config, code check, verify |
| `THROTTLE_PASSWORD_RESET` | `10/hour` | Request and confirm |

## Changes outside the app

- `core/organizations/services.create_organization`: the organization,
  main campus and org-admin in one place. `bootstrap_organization` now
  calls it.
- `integrations/email`: the `django` delivery option.
- The public application form takes `captcha_token`.
- OpenAPI: `TypeEnum` (organization type) and `SignupRequestStatusEnum`
  are pinned in `ENUM_NAME_OVERRIDES`.
- The tenant sweep gives each organization the signup it was created
  from. The public signup and reset views are listed with the reason they
  take no tenant input, and `/signup-requests/` is platform-only.

## Testing

23 tests (`core/signup/tests`), plus a CAPTCHA test on the public
application form:

- the whole flow: signup, nothing created yet, the link, the
  organization, campus and org-admin, signed in, the chosen password
  works, the link works once
- closed unless enabled; the config endpoint; a stray auth header ignored
- an existing email gets a note and no request; expired links and resend;
  a second signup replaces the first; a code taken by someone who verified
  first, or at the very same moment (409, not a crash)
- bad codes (taken, reserved, malformed), throwaway emails, weak
  passwords, unknown time zones; extra blocked domains; the code check
- the per-address mail cap; the purge command
- CAPTCHA required on signup and resend; the siteverify protocol, the v3
  score, and failing closed when the provider is down
- approval: queued, platform admins told, code and email held, approve,
  reject (code freed), platform admins only
- password reset: signs out everywhere, works once; nothing sent to
  unknown, inactive, API-key or closed-organization users; bad links and
  weak passwords; clears a lockout

Live, against a real server with real mail files: 32/32 checks in open
mode and 35/35 with approval on. Both runs cover the new admin adding a
campus and a user, and no server errors.
