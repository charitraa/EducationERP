from django.apps import AppConfig


class PermissionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core.permissions"
    label = "rbac"
    verbose_name = "Roles & Permissions"
