from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: the trail must never be edited from the admin."""

    list_display = ["created_at", "actor_email", "action", "module", "object_repr"]
    list_filter = ["action", "module", "created_at"]
    search_fields = ["actor_email", "object_repr", "object_type", "object_id"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
