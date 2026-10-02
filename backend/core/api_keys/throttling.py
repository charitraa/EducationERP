from rest_framework.throttling import SimpleRateThrottle

from .models import ApiKey


class ApiKeyRateThrottle(SimpleRateThrottle):
    """Each key's own limit (``ApiKey.rate_limit``), or the ``api_key`` rate.
    Does nothing for requests made with a person's login."""

    scope = "api_key"

    def __init__(self):
        # The rate depends on the key, so it's read per request in allow_request.
        pass

    def allow_request(self, request, view):
        key = request.auth if isinstance(request.auth, ApiKey) else None
        if key is None:
            return True
        self.rate = key.rate_limit or self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": request.auth.pk}
