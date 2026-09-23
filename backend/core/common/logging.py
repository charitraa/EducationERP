"""Request context for log records.

A traceback on its own rarely identifies which request produced it. Every
request carries an id, and this filter copies it — plus the user, method and
path — onto each log record so the 500 in the log file can be matched to the
id the client was handed back in the ``X-Request-ID`` header.

The id is kept in a ContextVar rather than on the request object, so logging
still works in code too far from the view to receive a request.
"""
import logging
from contextvars import ContextVar

from core.audit.middleware import get_client_ip, get_current_request

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str):
    """Set the id for the current context; returns a token for resetting."""
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


class RequestContextFilter(logging.Filter):
    """Attach request attributes to every record, blank when there is none.

    A filter never blocks a record here — it only enriches it. Missing
    attributes would make the formatter raise and lose the log line
    entirely, so each one always gets a value.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()

        request = get_current_request()
        if request is None:
            record.method = "-"
            record.path = "-"
            record.user_id = "-"
            record.client_ip = "-"
            return True

        record.method = request.method or "-"
        record.path = request.get_full_path()
        record.client_ip = get_client_ip(request) or "-"

        # request.user is absent if the failure happened before authentication.
        user = getattr(request, "user", None)
        record.user_id = (
            str(user.pk) if user is not None and getattr(user, "is_authenticated", False)
            else "anonymous"
        )
        return True
