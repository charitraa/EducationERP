from django.apps import AppConfig


class StaffConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.staff"
    label = "staff"
    verbose_name = "Staff"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
