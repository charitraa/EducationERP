from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from core.common.views import health, ready

urlpatterns = [
    path("admin/", admin.site.urls),
    # Versioned API. New versions get their own module beside api_v1;
    # v1 URLs never change shape once clients depend on them.
    path("api/v1/", include(("config.api_v1", "v1"), namespace="v1")),
    # Documentation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # Probes
    path("health/", health, name="health"),
    path("ready/", ready, name="ready"),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
