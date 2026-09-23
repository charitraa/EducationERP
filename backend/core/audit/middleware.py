"""Makes the current request available to code that is far from the view.

Model signals and service functions need the actor and IP for audit entries
but don't receive the request. A context variable carries it, scoped to the
request and safe under both threads and async.
"""
import ipaddress
from contextvars import ContextVar

_current_request: ContextVar = ContextVar("current_request", default=None)


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            _current_request.reset(token)


def get_current_request():
    return _current_request.get()


def get_current_user():
    request = get_current_request()
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def get_client_ip(request) -> str | None:
    """The client's IP, trusting only the proxies we were told about.

    X-Forwarded-For is written by the client first and by each proxy after,
    so only the entries appended by our own proxies can be believed. Taking
    the first entry — the old behaviour — let any client put a made-up
    address into the audit trail. This defers to DRF's rule, driven by
    ``NUM_PROXIES``, so the audit log and the rate limiter always agree on
    who the client is.
    """
    if request is None:
        return None
    from rest_framework.throttling import BaseThrottle

    ident = BaseThrottle().get_ident(request)
    try:
        return str(ipaddress.ip_address(ident))
    except (TypeError, ValueError):
        return None
