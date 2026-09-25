from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerSplitView,
)
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication

from core.common.permissions import ApiDocsAccess
from core.common.views import health, ready

# Session auth too, so a staff member logged in at /admin/ can open the docs
# in a browser when they are not public.
_docs = {
    "permission_classes": [ApiDocsAccess],
    "authentication_classes": [SessionAuthentication, JWTAuthentication],
}

urlpatterns = [
    path("admin/", admin.site.urls),
    # Versioned API. New versions get their own module beside api_v1;
    # v1 URLs never change shape once clients depend on them.
    path("api/v1/", include(("config.api_v1", "v1"), namespace="v1")),
    # Documentation
    path("api/schema/", SpectacularAPIView.as_view(**_docs), name="schema"),
    # The split view loads its script from a URL instead of inline, which a
    # strict Content-Security-Policy requires.
    path(
        "api/docs/",
        SpectacularSwaggerSplitView.as_view(url_name="schema", **_docs),
        name="swagger-ui",
    ),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema", **_docs), name="redoc"),
    # Probes
    # ZKTeco attendance devices push to these fixed paths (no /api/ prefix).
    path("iclock/", include("integrations.biometric.urls")),
    path("health/", health, name="health"),
    path("ready/", ready, name="ready"),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
