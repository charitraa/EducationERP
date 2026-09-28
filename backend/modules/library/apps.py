from django.apps import AppConfig


class LibraryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.library"
    label = "library"
    verbose_name = "Library"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
