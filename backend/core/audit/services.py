"""Write-side API for the audit trail.

Every module records through these helpers rather than creating AuditLog rows
directly, so the shape of the trail stays consistent platform-wide.
"""
import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from django.db import models

from .middleware import get_client_ip, get_current_request, get_current_user
from .models import AuditLog

logger = logging.getLogger(__name__)

# Never copied into the trail, even if a model happens to expose them.
SENSITIVE_FIELDS = {"password", "token", "secret", "api_key", "access", "refresh"}

# Bookkeeping fields that change on every save; recording them would turn a
# no-op update into an audit entry. The log row carries its own timestamp.
IGNORED_FIELDS = {"updated_at"}


def _serialize(value):
    """Make a field value safe for JSON storage."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, models.Model):
        return value.pk
    return str(value)


def snapshot(instance) -> dict:
    """Capture an instance's concrete field values before a change."""
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in SENSITIVE_FIELDS or field.name in IGNORED_FIELDS:
            continue
        data[field.name] = _serialize(getattr(instance, field.attname, None))
    return data


def diff(before: dict, after: dict) -> dict:
    """``{field: {'before': x, 'after': y}}`` for fields that actually changed."""
    changes = {}
    for key, new_value in after.items():
        old_value = before.get(key)
        if old_value != new_value:
            changes[key] = {"before": old_value, "after": new_value}
    return changes


def _organization_of(instance, actor):
    from core.organizations.models import Organization

    if isinstance(instance, Organization):
        return instance
    org_id = getattr(instance, "organization_id", None)
    if org_id:
        return org_id
    return getattr(actor, "organization_id", None) if actor else None


def log(
    action: str,
    *,
    instance=None,
    module: str = "",
    changes: dict | None = None,
    metadata: dict | None = None,
    actor=None,
    organization=None,
    request=None,
) -> AuditLog | None:
    """Record one audited action.

    Never raises: a failure to write the trail must not fail the operation the
    user asked for. Failures are logged instead.
    """
    try:
        request = request or get_current_request()
        actor = actor or get_current_user()

        if organization is None and instance is not None:
            organization = _organization_of(instance, actor)
        elif organization is None and actor is not None:
            organization = actor.organization_id

        entry = AuditLog(
            actor=actor,
            actor_email=getattr(actor, "email", "") or "",
            action=action,
            module=module or (instance._meta.app_label if instance is not None else ""),
            changes=changes or {},
            metadata=metadata or {},
        )

        if isinstance(organization, models.Model):
            entry.organization = organization
        elif organization:
            entry.organization_id = organization

        if instance is not None:
            entry.object_type = instance._meta.label  # e.g. "accounts.User"
            entry.object_id = str(instance.pk or "")
            entry.object_repr = str(instance)[:255]

        if request is not None:
            entry.ip_address = get_client_ip(request)
            entry.user_agent = request.META.get("HTTP_USER_AGENT", "")[:512]
            entry.request_path = request.path[:512]
            entry.request_method = request.method or ""

        entry.save()
        return entry
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to write audit log entry (action=%s)", action)
        return None


def log_create(request, instance, module: str = "") -> AuditLog | None:
    return log(
        AuditLog.Action.CREATE,
        instance=instance,
        module=module,
        changes={k: {"before": None, "after": v} for k, v in snapshot(instance).items()},
        request=request,
    )


def log_update(request, instance, before: dict, module: str = "") -> AuditLog | None:
    changes = diff(before, snapshot(instance))
    if not changes:
        return None
    return log(
        AuditLog.Action.UPDATE,
        instance=instance,
        module=module,
        changes=changes,
        request=request,
    )


def log_delete(request, instance, module: str = "") -> AuditLog | None:
    return log(
        AuditLog.Action.DELETE,
        instance=instance,
        module=module,
        changes={k: {"before": v, "after": None} for k, v in snapshot(instance).items()},
        request=request,
    )


def log_login_failure(email, reason: str, request=None) -> AuditLog | None:
    """Record a failed login attempt: wrong password, wrong code or locked.

    Filed under the account's organization so its admins see password
    guessing against their users. An unknown email stays platform-level —
    no tenant owns it — and the caller learns nothing either way.
    """
    from django.contrib.auth import get_user_model

    email = (email or "").lower().strip()
    organization_id = (
        get_user_model()
        .all_objects.filter(email=email)
        .values_list("organization_id", flat=True)
        .first()
    )
    return log(
        AuditLog.Action.LOGIN_FAILED,
        module="authentication",
        organization=organization_id,
        metadata={"email": email, "reason": reason},
        request=request or get_current_request(),
    )
