from django.apps import AppConfig


class AttendanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.attendance"
    label = "attendance"
    verbose_name = "Attendance"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
