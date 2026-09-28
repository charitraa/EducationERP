from django.apps import AppConfig


class NoticesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.notices"
    label = "notices"
    verbose_name = "Notices"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
