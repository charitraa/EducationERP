"""Makes the current request available to code that is far from the view.

Model signals and service functions need the actor and IP for audit entries
but don't receive the request. A context variable carries it, scoped to the
request and safe under both threads and async.
"""
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
    """Client IP, honouring one layer of trusted proxy."""
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
