"""Version 1 of the public API.

Phase 1 exposes the identity foundation. Later phases append their module
routes here — the prefixes are already reserved in the project plan
(/api/v1/students/, /api/v1/attendance/, ...).
"""
from django.urls import include, path

urlpatterns = [
    path("auth/", include("core.authentication.urls")),
    path("", include("core.organizations.urls")),
    path("", include("core.accounts.urls")),
    path("", include("core.permissions.urls")),
    path("", include("core.audit.urls")),
    # Phase 2
    path("", include("modules.students.urls")),
    path("", include("modules.parents.urls")),
    path("", include("modules.staff.urls")),
    path("", include("modules.admissions.urls")),
]
