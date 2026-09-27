from django.apps import AppConfig


class FinanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.finance"
    label = "finance"
    verbose_name = "Finance"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
