from django.contrib import admin

from .models import Permission, Role, UserRole


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ["code", "module", "action", "name"]
    list_filter = ["module"]
    search_fields = ["code", "name"]
    # Managed by the sync_permissions command, not by hand.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "organization", "is_system"]
    list_filter = ["is_system", "organization"]
    search_fields = ["name", "code"]
    filter_horizontal = ["permissions"]


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "campus", "expires_at", "created_at"]
    list_filter = ["role", "campus"]
    search_fields = ["user__email", "role__code"]
    autocomplete_fields = ["user", "role", "campus"]
