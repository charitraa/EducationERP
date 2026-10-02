"""Public signup and password reset.

Signup: ``start_signup`` stores a request and mails a link;
``verify_signup`` follows it and either creates the organization or queues
it for approval (``approve`` / ``reject``). Password reset uses Django's
token generator: the token is tied to the current password hash and last
login, so it stops working once used, and expires after
``PASSWORD_RESET_TIMEOUT``.
"""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.tokens import default_token_generator
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from core.accounts.models import User
from core.audit.models import AuditLog
from core.audit.services import log
from core.authentication import lockout
from core.common.exceptions import ConflictError, ServiceError
from core.organizations.models import Organization
from core.organizations.services import create_organization

from . import mail
from .models import SignupRequest

MODULE = "signup"

# Codes a person can't pick: they'd look official, or clash with the
# addresses a hosted frontend gives itself (code.example.com).
RESERVED_CODES = frozenset("""
admin administrator api app apps auth billing blog cdn dashboard demo dev docs help login logout
mail media my official platform root signup static status support system test www
""".split())


# ---------------------------------------------------------------------------
# Organization codes
# ---------------------------------------------------------------------------
def code_unavailable_reason(code: str) -> str | None:
    """``"reserved"`` or ``"taken"``, or ``None`` when ``code`` is free. A
    verified signup waiting for approval holds its code; an unverified one
    doesn't."""
    code = code.lower()
    if code in RESERVED_CODES:
        return "reserved"
    if Organization.all_objects.filter(code=code).exists():
        return "taken"
    if SignupRequest.objects.filter(organization_code=code,
                                    status=SignupRequest.Status.AWAITING_APPROVAL).exists():
        return "taken"
    return None


def _email_taken(email: str) -> bool:
    return (User.all_objects.filter(email=email).exists()
            or SignupRequest.objects.filter(admin_email=email,
                                            status=SignupRequest.Status.AWAITING_APPROVAL).exists())


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------
def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _issue_token(request: SignupRequest) -> str:
    token = secrets.token_urlsafe(32)
    request.token_hash = _digest(token)
    request.token_expires_at = timezone.now() + timedelta(hours=settings.SIGNUP_TOKEN_HOURS)
    return token


def _send_link(request: SignupRequest, token: str) -> None:
    link = settings.SIGNUP_VERIFY_URL.format(token=token)
    mail.send(to=request.admin_email, subject=f"Confirm your email to create {request.organization_name}",
              body=(f"Hello {request.admin_first_name},\n\n"
                    f"Open this link to confirm your email and create {request.organization_name}:\n\n"
                    f"{link}\n\n"
                    f"It works for {settings.SIGNUP_TOKEN_HOURS} hours. "
                    "If you didn't sign up, ignore this email and nothing will be created."))


@transaction.atomic
def start_signup(*, organization_name: str, organization_code: str, admin_email: str, password: str,
                 admin_first_name: str, admin_last_name: str = "", admin_phone: str = "",
                 organization_type: str = Organization.Type.COLLEGE, timezone_name: str = "UTC",
                 ip_address: str | None = None) -> SignupRequest | None:
    """Stores the request and mails the link. An email that already has an
    account gets a note saying so instead, and ``None`` comes back; the
    caller answers the same way in both cases, so the form doesn't reveal
    who has an account."""
    email = admin_email.lower().strip()
    if _email_taken(email):
        mail.send(to=email, subject="You already have an account",
                  body=("Someone (hopefully you) tried to sign up a new organization with this email, "
                        "but it already has an account. Sign in, or use \"Forgot password\" if you "
                        "can't remember your password. If it wasn't you, ignore this email."))
        return None

    # A second signup from the same address replaces an unfinished one, so
    # only the newest link works.
    SignupRequest.objects.filter(admin_email=email, status=SignupRequest.Status.PENDING).delete()
    request = SignupRequest(
        organization_name=organization_name, organization_code=organization_code.lower(),
        organization_type=organization_type, timezone=timezone_name, admin_email=email,
        admin_first_name=admin_first_name, admin_last_name=admin_last_name, admin_phone=admin_phone,
        password_hash=make_password(password), ip_address=ip_address,
    )
    token = _issue_token(request)
    request.save()
    _send_link(request, token)
    return request


@transaction.atomic
def resend_link(email: str) -> None:
    """A fresh link for an unfinished signup; the old one stops working.
    Silent when there's nothing to resend."""
    request = (SignupRequest.objects.select_for_update()
               .filter(admin_email=email.lower().strip(), status=SignupRequest.Status.PENDING).first())
    if request is None:
        return
    token = _issue_token(request)
    request.save(update_fields=["token_hash", "token_expires_at", "updated_at"])
    _send_link(request, token)


def _ensure_still_free(request: SignupRequest) -> None:
    """The code and email were free at signup; someone may have taken them
    since (only a verified request holds them)."""
    awaiting = SignupRequest.objects.filter(status=SignupRequest.Status.AWAITING_APPROVAL).exclude(pk=request.pk)
    code = request.organization_code
    if Organization.all_objects.filter(code=code).exists() or awaiting.filter(organization_code=code).exists():
        raise ConflictError(f"The code '{code}' was taken in the meantime. Sign up again with another one.",
                            code="code_taken")
    email = request.admin_email
    if User.all_objects.filter(email=email).exists() or awaiting.filter(admin_email=email).exists():
        raise ConflictError("This email has an account now. Sign in instead.", code="email_taken")


def _create(request: SignupRequest, *, by=None):
    try:
        # A savepoint: two links for the same code opened at the same
        # instant both pass _ensure_still_free; the database decides.
        with transaction.atomic():
            organization, _, admin = _create_organization(request)
    except IntegrityError as exc:
        raise ConflictError(f"The code '{request.organization_code}' or the email was taken in the "
                            "meantime. Sign up again.", code="code_taken") from exc
    request.status, request.organization = SignupRequest.Status.COMPLETED, organization
    request.password_hash = ""  # lives on the user now
    log(AuditLog.Action.CREATE, instance=organization, module=MODULE, actor=by or admin,
        organization=organization.pk, metadata={"signup_request": request.pk})
    return organization, admin


def _create_organization(request: SignupRequest):
    return create_organization(
        name=request.organization_name, code=request.organization_code, type=request.organization_type,
        timezone=request.timezone, email=request.admin_email, admin_email=request.admin_email,
        admin_password_hash=request.password_hash, admin_first_name=request.admin_first_name,
        admin_last_name=request.admin_last_name, admin_phone=request.admin_phone,
    )


@transaction.atomic
def verify_signup(token: str):
    """Returns ``(request, admin)``; ``admin`` is ``None`` while the
    request waits for a platform admin's approval."""
    request = (SignupRequest.objects.select_for_update()
               .filter(token_hash=_digest(token or ""), status=SignupRequest.Status.PENDING).first())
    if request is None:
        raise ServiceError("This link isn't valid. It may have been used already, or replaced by a newer one.",
                           code="invalid_token")
    if request.token_expires_at <= timezone.now():
        raise ServiceError("This link has expired. Ask for a new one.", code="expired_token")
    _ensure_still_free(request)

    request.verified_at, request.token_hash = timezone.now(), ""
    admin = None
    if settings.SIGNUP_REQUIRE_APPROVAL:
        request.status = SignupRequest.Status.AWAITING_APPROVAL
        for platform_admin in User.objects.filter(is_superuser=True, organization=None, is_active=True):
            mail.send(to=platform_admin.email, subject=f"Signup waiting for approval: {request.organization_name}",
                      body=f"{request.organization_name} ({request.organization_code}), signed up by "
                           f"{request.admin_email}, has verified its email and waits for your approval.")
    else:
        _, admin = _create(request)
    request.save()
    return request, admin


def _locked_awaiting(request: SignupRequest) -> SignupRequest:
    request = SignupRequest.objects.select_for_update().get(pk=request.pk)
    if request.status != SignupRequest.Status.AWAITING_APPROVAL:
        raise ConflictError("Only a verified signup waiting for approval can be decided.",
                            code="not_awaiting_approval")
    return request


@transaction.atomic
def approve(request: SignupRequest, *, by) -> SignupRequest:
    request = _locked_awaiting(request)
    _ensure_still_free(request)
    organization, _ = _create(request, by=by)
    request.decided_at, request.decided_by = timezone.now(), by
    request.save()
    mail.send(to=request.admin_email, subject=f"{organization.name} is ready",
              body=f"Your organization {organization.name} has been approved. "
                   "Sign in with the email and password you chose when you signed up.")
    return request


@transaction.atomic
def reject(request: SignupRequest, *, by, reason: str) -> SignupRequest:
    request = _locked_awaiting(request)
    request.status, request.rejection_reason = SignupRequest.Status.REJECTED, reason
    request.decided_at, request.decided_by, request.password_hash = timezone.now(), by, ""
    request.save()
    log(AuditLog.Action.UPDATE, instance=request, module=MODULE, actor=by,
        changes={"status": {"before": SignupRequest.Status.AWAITING_APPROVAL, "after": request.status}},
        metadata={"reason": reason})
    mail.send(to=request.admin_email, subject=f"About your signup for {request.organization_name}",
              body="Your signup was not approved." + (f"\n\nReason: {reason}" if reason else ""))
    return request


def purge_unverified(*, older_than_days: int = 7) -> int:
    """Delete unverified requests whose link expired that long ago."""
    cutoff = timezone.now() - timedelta(days=older_than_days)
    deleted, _ = SignupRequest.objects.filter(status=SignupRequest.Status.PENDING,
                                              token_expires_at__lt=cutoff).delete()
    return deleted


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------
def request_password_reset(email: str) -> None:
    """Mails a reset link if ``email`` belongs to an active person in an
    active organization; silent otherwise, so nothing is revealed."""
    user = (User.objects.filter(email=email.lower().strip(), is_active=True)
            .exclude(user_type=User.Type.INTEGRATION).select_related("organization").first())
    if user is None or (user.organization_id and not user.organization.is_active):
        return
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    link = settings.PASSWORD_RESET_URL.format(uid=uid, token=default_token_generator.make_token(user))
    minutes = settings.PASSWORD_RESET_TIMEOUT // 60
    mail.send(to=user.email, subject="Reset your password",
              body=(f"Open this link to choose a new password:\n\n{link}\n\n"
                    f"It works for {minutes} minutes and only once. "
                    "If you didn't ask for this, ignore this email; your password stays the same."))


def user_for_reset(uid: str, token: str) -> User:
    try:
        pk = int(force_str(urlsafe_base64_decode(uid)))
    except (TypeError, ValueError, OverflowError):
        pk = None
    user = User.objects.filter(pk=pk, is_active=True).exclude(user_type=User.Type.INTEGRATION).first() \
        if pk else None
    if user is None or not default_token_generator.check_token(user, token):
        raise ServiceError("This reset link isn't valid or has expired. Ask for a new one.", code="invalid_token")
    return user


@transaction.atomic
def reset_password(user: User, new_password: str) -> None:
    """Sets the password and signs the account out everywhere: whoever had
    the old password may also hold a refresh token."""
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])
    for outstanding in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=outstanding)
    lockout.reset(lockout.ACCOUNT, user.email)
    log(AuditLog.Action.PASSWORD_CHANGE, instance=user, module="accounts", actor=user,
        metadata={"self_service": True, "reset_by_email": True})
