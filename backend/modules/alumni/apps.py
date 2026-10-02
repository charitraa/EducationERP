from django.apps import AppConfig


class AlumniConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.alumni"
    label = "alumni"
    verbose_name = "Alumni"

    def ready(self):
        from . import permissions, receivers  # noqa: F401  (registers the catalogue and the graduation hook)
