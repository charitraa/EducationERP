from django.contrib.admin.apps import AdminConfig


class ERPAdminConfig(AdminConfig):
    """Replaces ``django.contrib.admin`` in INSTALLED_APPS so the default
    ``admin.site`` is our hardened site. Kept free of model imports: this
    module is loaded before the app registry is ready."""

    default_site = "core.authentication.admin_site.ERPAdminSite"
