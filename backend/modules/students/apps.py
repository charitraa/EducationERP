from django.apps import AppConfig


class StudentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.students"
    label = "students"
    verbose_name = "Students"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
