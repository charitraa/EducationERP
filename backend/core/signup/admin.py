from django.contrib import admin

from .models import SignupRequest


@admin.register(SignupRequest)
class SignupRequestAdmin(admin.ModelAdmin):
    list_display = ["organization_name", "organization_code", "admin_email", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["organization_name", "organization_code", "admin_email"]
    exclude = ["password_hash", "token_hash"]
    readonly_fields = ["organization", "verified_at", "decided_at", "decided_by", "ip_address"]
