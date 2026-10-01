from django.apps import AppConfig


class HrConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.hr"
    label = "hr"
    verbose_name = "HR"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
