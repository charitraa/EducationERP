"""URLconf used only by the error-logging tests."""
from django.urls import path

from core.common.views import health


def boom(request):
    raise RuntimeError("boom")


urlpatterns = [
    path("api/v1/boom/", boom, name="boom"),
    path("health/", health, name="health"),
]
