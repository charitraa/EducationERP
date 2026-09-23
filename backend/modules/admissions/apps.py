from django.apps import AppConfig


class AdmissionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.admissions"
    label = "admissions"
    verbose_name = "Admissions"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
