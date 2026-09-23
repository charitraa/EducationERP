"""Brute-force protection shared by the API login and the Django admin login.

The per-IP rate limit alone does not stop password guessing spread across
many addresses, so failures are also counted per *account*. After
``LOGIN_LOCKOUT_ATTEMPTS`` failures the account refuses every login attempt —
even the right password — until ``LOGIN_LOCKOUT_MINUTES`` pass with no
further failures.

Counters live in the cache (Redis in production, so every worker and replica
shares them). Keys are hashed, so the cache never holds email addresses, and
unknown emails are counted exactly like real ones — a lockout reveals nothing
about whether an account exists.

Trade-off: someone who knows an email can keep that account locked by failing
on purpose. The window is short so the owner is never locked out for long,
and every failed attempt is in the audit log.
"""
import hashlib

from django.conf import settings
from django.core.cache import cache

ACCOUNT = "account"
IP = "ip"

# An address tries many accounts, so it gets more room than one account.
_IP_MULTIPLIER = 4


def _key(kind: str, value: str) -> str:
    digest = hashlib.sha256((value or "").lower().strip().encode()).hexdigest()
    return f"login-guard:{kind}:{digest}"


def _limit(kind: str) -> int:
    attempts = settings.LOGIN_LOCKOUT_ATTEMPTS
    return attempts * _IP_MULTIPLIER if kind == IP else attempts


def window_seconds() -> int:
    return settings.LOGIN_LOCKOUT_MINUTES * 60


def is_locked(kind: str, value: str) -> bool:
    return (cache.get(_key(kind, value)) or 0) >= _limit(kind)


def record_failure(kind: str, value: str) -> None:
    key = _key(kind, value)
    # add() + incr() is atomic on Redis, so concurrent guesses can't slip
    # through between a read and a write. touch() restarts the window.
    if not cache.add(key, 1, timeout=window_seconds()):
        try:
            cache.incr(key)
        except ValueError:  # expired between add() and incr()
            cache.set(key, 1, timeout=window_seconds())
        cache.touch(key, timeout=window_seconds())


def reset(kind: str, value: str) -> None:
    cache.delete(_key(kind, value))
