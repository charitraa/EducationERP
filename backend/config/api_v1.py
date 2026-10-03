"""Version 1 of the public API.

Every module's ``urls.py`` is included here. Order follows INSTALLED_APPS:
the platform (``core``) first, then the business modules.
"""
from django.urls import include, path

urlpatterns = [
    path("auth/", include("core.authentication.urls")),
    path("", include("core.organizations.urls")),
    path("", include("core.accounts.urls")),
    path("", include("core.permissions.urls")),
    path("", include("core.audit.urls")),
    path("", include("core.files.urls")),
    path("", include("core.api_keys.urls")),
    path("", include("core.signup.urls")),
    path("", include("modules.students.urls")),
    path("", include("modules.parents.urls")),
    path("", include("modules.staff.urls")),
    path("", include("modules.admissions.urls")),
    path("", include("modules.academics.urls")),
    path("", include("modules.timetable.urls")),
    path("", include("modules.attendance.urls")),
    path("", include("modules.examinations.urls")),
    path("", include("modules.finance.urls")),
    path("", include("modules.events.urls")),
    path("", include("modules.notifications.urls")),
    path("", include("modules.notices.urls")),
    path("", include("modules.communication.urls")),
    path("", include("modules.support.urls")),
    path("", include("modules.library.urls")),
    path("", include("modules.inventory.urls")),
    path("", include("modules.hr.urls")),
    path("", include("modules.payroll.urls")),
    path("", include("modules.hostel.urls")),
    path("", include("modules.transport.urls")),
    path("", include("modules.applications.urls")),
    path("", include("modules.alumni.urls")),
    path("", include("modules.careers.urls")),
]
