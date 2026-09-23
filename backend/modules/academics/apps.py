from django.apps import AppConfig


class AcademicsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.academics"
    label = "academics"
    verbose_name = "Academics"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
