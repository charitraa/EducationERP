"""SMS delivery: one ``send`` every backend implements the same way.

The console backend just logs what would have been sent — swap
``get_backend`` for a real gateway when the platform actually needs to send
text messages. ``notifications.services.notify`` is the usual caller; the one
exception is ``admissions.services._notify_application_approved``, which
reaches an applicant who has no ``User`` account yet, so ``notify``'s in-app
``Notification`` (which only ever addresses a ``User``) can't be used.
"""
import logging

logger = logging.getLogger("integrations.sms")


def send_console(*, to: str, message: str) -> bool:
    logger.info("SMS to=%s message=%r", to, message)
    return True


def get_backend():
    return send_console


def send(*, to: str, message: str) -> bool:
    if not to:
        return False
    return get_backend()(to=to, message=message)
