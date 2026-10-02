from django.contrib import admin

from .models import ApiKey


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "prefix", "read_only", "expires_at", "revoked_at", "last_used_at"]
    readonly_fields = ["prefix", "secret_hash", "user", "last_used_at", "last_used_ip"]
