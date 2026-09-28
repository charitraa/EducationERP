"""Push delivery: one ``send`` every backend implements the same way.

The console backend just logs what would have been sent — swap
``get_backend`` for a real push provider (FCM, APNs, ...) when the platform
actually needs to push to a device. ``notifications.services.notify`` is the
only caller; nothing else should reach into this module directly.
"""
import logging

logger = logging.getLogger("integrations.push")


def send_console(*, to: str, title: str, body: str) -> bool:
    logger.info("PUSH to=%s title=%r body=%r", to, title, body)
    return True


def get_backend():
    return send_console


def send(*, to: str, title: str, body: str = "") -> bool:
    if not to:
        return False
    return get_backend()(to=to, title=title, body=body)
