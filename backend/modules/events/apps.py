from django.apps import AppConfig


class EventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.events"
    label = "events"
    verbose_name = "Events"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
