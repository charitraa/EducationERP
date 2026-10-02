"""Mail to an address typed into a public form, at most
``EMAILS_PER_ADDRESS_PER_HOUR`` an hour. Beyond that it is quietly not sent:
the response must look the same either way, or the form would reveal which
addresses have accounts."""
import hashlib

from django.conf import settings
from django.core.cache import cache

from integrations.email import base as email_backend

WINDOW_SECONDS = 3600


def send(*, to: str, subject: str, body: str) -> bool:
    key = "mail-per-address:" + hashlib.sha256(to.lower().encode()).hexdigest()
    cache.add(key, 0, WINDOW_SECONDS)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, WINDOW_SECONDS)
        count = 1
    if count > settings.EMAILS_PER_ADDRESS_PER_HOUR:
        return False
    return email_backend.send(to=to, subject=subject, body=body)
