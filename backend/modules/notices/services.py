"""Notice lifecycle: draft, then published (optionally with an expiry)."""
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError

from .models import Notice

MODULE = "notices"


def publish_notice(notice: Notice, *, expires_at=None, by=None) -> Notice:
    if notice.is_published:
        raise ConflictError("Already published.", code="already_published")
    notice.published_at = timezone.now()
    if expires_at is not None:
        notice.expires_at = expires_at
    notice.save(update_fields=["published_at", "expires_at", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=notice, module=MODULE, actor=by,
        changes={"published_at": {"before": None, "after": str(notice.published_at)}})
    return notice
