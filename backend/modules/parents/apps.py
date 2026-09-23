from django.apps import AppConfig


class ParentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.parents"
    label = "parents"
    verbose_name = "Parents"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
