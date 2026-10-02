"""Creating, revoking and rotating API keys."""
from django.db import transaction
from django.utils import timezone

from core.accounts.models import User
from core.accounts.services import assign_role
from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError

from . import keys
from .models import ApiKey

MODULE = "api_keys"


def _fresh_prefix() -> str:
    while True:
        prefix = keys.new_prefix()
        if not ApiKey.all_objects.filter(prefix=prefix).exists():
            return prefix


@transaction.atomic
def create_api_key(*, organization_id: int, name: str, by=None, roles=(), **fields) -> tuple[ApiKey, str]:
    """Returns the key and its secret, which is never shown again. ``roles``
    is ``[(role, campus or None)]``; each grant obeys the same rule as for a
    person (``ensure_can_grant``): no more than ``by`` holds."""
    prefix, secret = _fresh_prefix(), keys.new_secret()
    user = User.objects.create_user(email=f"key-{prefix}@api-keys.invalid", password=None,
                                    organization_id=organization_id, user_type=User.Type.INTEGRATION,
                                    first_name=name[:150], last_name="(API key)")
    key = ApiKey.objects.create(organization_id=organization_id, user=user, name=name, prefix=prefix,
                                secret_hash=keys.digest(secret), created_by=by, **fields)
    for role, campus in roles:
        assign_role(user=user, role=role, campus=campus, granted_by=by)
    log(AuditLog.Action.CREATE, instance=key, module=MODULE, actor=by)
    return key, keys.compose(prefix, secret)


@transaction.atomic
def revoke_api_key(key: ApiKey, *, reason: str = "", by=None) -> ApiKey:
    key = ApiKey.objects.select_for_update().get(pk=key.pk)
    if key.revoked_at is not None:
        raise ConflictError("Already revoked.", code="already_revoked")
    key.revoked_at, key.revoked_by, key.revoked_reason = timezone.now(), by, reason
    key.save(update_fields=["revoked_at", "revoked_by", "revoked_reason", "updated_at"])
    key.user.is_active = False
    key.user.save(update_fields=["is_active", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=key, module=MODULE, actor=by,
        changes={"revoked_at": {"before": None, "after": key.revoked_at.isoformat()}}, metadata={"reason": reason})
    return key


@transaction.atomic
def rotate_api_key(key: ApiKey, *, by=None) -> tuple[ApiKey, str]:
    """A new secret (and prefix); the old one stops working at once."""
    key = ApiKey.objects.select_for_update().get(pk=key.pk)
    if key.revoked_at is not None:
        raise ConflictError("A revoked key can't be rotated; create a new one.", code="revoked")
    before = key.prefix
    key.prefix, secret = _fresh_prefix(), keys.new_secret()
    key.secret_hash = keys.digest(secret)
    key.save(update_fields=["prefix", "secret_hash", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=key, module=MODULE, actor=by,
        changes={"prefix": {"before": before, "after": key.prefix}}, metadata={"operation": "rotate"})
    return key, keys.compose(key.prefix, secret)
