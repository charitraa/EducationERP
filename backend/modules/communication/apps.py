from django.apps import AppConfig


class CommunicationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.communication"
    label = "communication"
    verbose_name = "Communication"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
