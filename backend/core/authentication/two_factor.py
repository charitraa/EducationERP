"""Two-factor login with an authenticator app (TOTP, RFC 6238).

Flow for a user:
    begin_setup()        → secret + otpauth:// URI to scan (not active yet)
    confirm_setup(code)  → active; returns single-use recovery codes, shown once
    verify(code)         → at login: a 6-digit app code, or one recovery code
    disable()            → off (the user after re-checking, or an admin reset)
"""
import hashlib
import hmac
import secrets
import time

import pyotp
from django.db import transaction
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, ServiceError

from .models import TwoFactor

ISSUER = "Education ERP"
RECOVERY_CODE_COUNT = 10
STEP_SECONDS = 30
# Accept the previous and next 30 s step too, for phones whose clocks drift.
DRIFT_STEPS = 1


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


def _new_recovery_codes(device: TwoFactor) -> list[str]:
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODE_COUNT)]
    device.recovery_codes = [_hash(c) for c in codes]
    return codes


def is_enabled(user) -> bool:
    return TwoFactor.objects.filter(user=user, confirmed_at__isnull=False).exists()


@transaction.atomic
def begin_setup(user) -> tuple[str, str]:
    """Start (or restart) setup. Returns the secret and its otpauth:// URI."""
    if is_enabled(user):
        raise ConflictError(
            "Two-factor login is already on. Turn it off first to set up a new app.",
            code="two_factor_enabled",
        )
    secret = pyotp.random_base32()
    TwoFactor.objects.update_or_create(
        user=user,
        defaults={"secret": secret, "confirmed_at": None, "last_used_step": None, "recovery_codes": []},
    )
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
    return secret, uri


def _accept_totp(device: TwoFactor, code: str) -> bool:
    """Check a 6-digit code, refusing any step already used."""
    totp = pyotp.TOTP(device.secret)
    current = int(time.time() // STEP_SECONDS)
    for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
        if device.last_used_step is not None and step <= device.last_used_step:
            continue
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), code):
            device.last_used_step = step
            device.save(update_fields=["last_used_step", "updated_at"])
            return True
    return False


@transaction.atomic
def confirm_setup(user, code: str) -> list[str]:
    device = TwoFactor.objects.select_for_update().filter(user=user, confirmed_at__isnull=True).first()
    if device is None:
        raise ServiceError("Start two-factor setup first.", code="no_pending_setup")
    if not _accept_totp(device, (code or "").strip()):
        raise ServiceError("That code is not valid. Check the time on your phone.", code="invalid_otp")

    codes = _new_recovery_codes(device)
    device.confirmed_at = timezone.now()
    device.save(update_fields=["confirmed_at", "recovery_codes", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=user, module="authentication", actor=user,
        metadata={"operation": "two_factor_enabled"})
    return codes


@transaction.atomic
def verify(user, code: str) -> bool:
    """True for a valid app code or an unused recovery code (which is spent)."""
    device = TwoFactor.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
    code = (code or "").strip().replace(" ", "")
    if device is None or not code:
        return False
    if code.isdigit():
        return _accept_totp(device, code)

    hashed = _hash(code)
    if hashed in device.recovery_codes:
        device.recovery_codes = [h for h in device.recovery_codes if h != hashed]
        device.save(update_fields=["recovery_codes", "updated_at"])
        log(AuditLog.Action.LOGIN, instance=user, module="authentication", actor=user,
            metadata={"operation": "recovery_code_used", "remaining": len(device.recovery_codes)})
        return True
    return False


@transaction.atomic
def regenerate_recovery_codes(user) -> list[str]:
    device = TwoFactor.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
    if device is None:
        raise ServiceError("Two-factor login is not on.", code="two_factor_disabled")
    codes = _new_recovery_codes(device)
    device.save(update_fields=["recovery_codes", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=user, module="authentication", actor=user,
        metadata={"operation": "recovery_codes_regenerated"})
    return codes


@transaction.atomic
def disable(user, by=None) -> None:
    """Turn two-factor off: by the user, or by an admin resetting a lost phone."""
    deleted, _ = TwoFactor.objects.filter(user=user).delete()
    if deleted:
        log(AuditLog.Action.UPDATE, instance=user, module="authentication", actor=by or user,
            metadata={"operation": "two_factor_disabled", "by_admin": bool(by and by.pk != user.pk)})


def status(user) -> dict:
    device = TwoFactor.objects.filter(user=user).first()
    return {
        "enabled": bool(device and device.is_enabled),
        "pending_setup": bool(device and not device.is_enabled),
        "recovery_codes_left": len(device.recovery_codes) if device and device.is_enabled else 0,
    }
