from django.apps import AppConfig


class ApplicationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.applications"
    label = "applications"
    verbose_name = "Applications"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
