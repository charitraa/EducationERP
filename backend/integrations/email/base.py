"""Email delivery: one ``send`` every backend implements the same way.

The console backend just logs what would have been sent. Set
``EMAIL_DELIVERY=django`` to send through Django's mail settings (SMTP, or
any provider with a Django email backend): public signup and password
resets need mail that really arrives. ``notifications.services.notify`` is the usual
caller; the one exception is ``admissions.services._notify_application_approved``,
which reaches an applicant who has no ``User`` account yet, so ``notify``'s
in-app ``Notification`` (which only ever addresses a ``User``) can't be used.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger("integrations.email")


def send_console(*, to: str, subject: str, body: str) -> bool:
    logger.info("EMAIL to=%s subject=%r body=%r", to, subject, body)
    return True


def send_django(*, to: str, subject: str, body: str) -> bool:
    # A mail server that's down must not fail the request that sent the
    # mail (a signup, a payment); the person can ask for it again.
    try:
        return bool(send_mail(subject, body, None, [to]))
    except Exception:  # noqa: BLE001 (SMTP, socket and provider errors alike)
        logger.exception("EMAIL to=%s subject=%r failed", to, subject)
        return False


def get_backend():
    return send_django if settings.EMAIL_DELIVERY == "django" else send_console


def send(*, to: str, subject: str, body: str = "") -> bool:
    if not to:
        return False
    return get_backend()(to=to, subject=subject, body=body)
