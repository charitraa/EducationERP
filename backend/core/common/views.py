from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@extend_schema(tags=["system"], responses={200: dict})
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Liveness probe — the process is up and serving."""
    return Response({"status": "ok"})


@extend_schema(tags=["system"], responses={200: dict, 503: dict})
@api_view(["GET"])
@permission_classes([AllowAny])
def ready(request):
    """Readiness probe — dependencies (database and cache) are reachable."""
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - exercised only on real outages
        checks["database"] = f"error: {exc}"

    # A round trip, not just a ping: the throttle needs writes to work too.
    try:
        cache.set("health:ready-probe", "ok", timeout=10)
        read_back = cache.get("health:ready-probe")
        checks["cache"] = "ok" if read_back == "ok" else "error: read back failed"
    except Exception as exc:  # pragma: no cover - exercised only on real outages
        checks["cache"] = f"error: {exc}"

    healthy = all(value == "ok" for value in checks.values())
    return Response(
        {"status": "ready" if healthy else "unavailable", "checks": checks},
        status=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
