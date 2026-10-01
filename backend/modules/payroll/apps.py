from django.apps import AppConfig


class PayrollConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.payroll"
    label = "payroll"
    verbose_name = "Payroll"

    def ready(self):
        from . import permissions  # noqa: F401  (registers the catalogue)
