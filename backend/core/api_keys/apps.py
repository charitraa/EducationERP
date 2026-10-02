from django.apps import AppConfig


class ApiKeysConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core.api_keys"
    label = "api_keys"
    verbose_name = "API keys"

    def ready(self):
        from core.permissions.registry import PermissionSpec, register_permissions

        from . import schema  # noqa: F401  (documents the Api-Key scheme in OpenAPI)

        # Organization-level: org-admin holds it (grants_all); no other system role does.
        register_permissions([PermissionSpec("api_keys.manage", "Create, change, revoke and rotate API keys")])
