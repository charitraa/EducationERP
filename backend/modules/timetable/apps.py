from django.apps import AppConfig


class TimetableConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.timetable"
    label = "timetable"
    verbose_name = "Timetable"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
