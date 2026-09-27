from django.apps import AppConfig


class ExaminationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.examinations"
    label = "examinations"
    verbose_name = "Examinations"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
