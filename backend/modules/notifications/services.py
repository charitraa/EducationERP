"""The one way into notifications. Every module calls ``notify`` the moment
a business event happens (``StudentEnrolled``, ``PaymentReceived``,
``ExamResultPublished``, ...) so a notification rule lives in one place, not
copied into each module.
"""
from django.db import transaction
from django.utils import timezone

from integrations.email import base as email_backend
from integrations.push import base as push_backend
from integrations.sms import base as sms_backend

from .models import Notification


def notify(recipients, *, event_type: str, title: str, body: str = "", data: dict | None = None,
          organization_id=None) -> list[Notification]:
    """Create an in-app ``Notification`` for each recipient and fan it out
    through email/SMS/push. ``recipients`` is an iterable of ``User``; a
    duplicate or ``None`` entry is ignored so callers can pass e.g. a
    parent's several guardians without filtering first.
    """
    seen: set[int] = set()
    rows: list[Notification] = []
    with transaction.atomic():
        for user in recipients:
            if user is None or user.pk in seen:
                continue
            seen.add(user.pk)
            row = Notification.objects.create(
                organization_id=organization_id or user.organization_id, recipient=user, event_type=event_type,
                title=title, body=body, data=data or {},
            )
            rows.append(row)
            email_backend.send(to=user.email, subject=title, body=body)
            if user.phone:
                sms_backend.send(to=user.phone, message=f"{title}: {body}" if body else title)
            push_backend.send(to=str(user.pk), title=title, body=body)
    return rows


def notify_address(*, email: str = "", phone: str = "", title: str, body: str = "") -> None:
    """For someone with no account (a public applicant): email and SMS only,
    as there is no inbox to put an in-app notification in."""
    if email:
        email_backend.send(to=email, subject=title, body=body)
    if phone:
        sms_backend.send(to=phone, message=f"{title}: {body}" if body else title)


def mark_read(notification: Notification) -> Notification:
    if notification.is_read:
        return notification
    notification.is_read, notification.read_at = True, timezone.now()
    notification.save(update_fields=["is_read", "read_at", "updated_at"])
    return notification


def mark_all_read(user) -> int:
    return Notification.objects.filter(recipient=user, is_read=False).update(
        is_read=True, read_at=timezone.now())
