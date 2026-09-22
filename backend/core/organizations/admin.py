from django.contrib import admin

from .models import Campus, Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "type", "is_active", "created_at"]
    list_filter = ["type", "is_active"]
    search_fields = ["name", "code", "legal_name"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]


@admin.register(Campus)
class CampusAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "organization", "is_main", "is_active"]
    list_filter = ["is_active", "is_main", "organization"]
    search_fields = ["name", "code", "city"]
    autocomplete_fields = ["organization"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
