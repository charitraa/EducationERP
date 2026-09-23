from django.contrib import admin

from .models import StaffMember


@admin.register(StaffMember)
class StaffMemberAdmin(admin.ModelAdmin):
    list_display = ["employee_number", "first_name", "last_name", "designation", "campus", "status"]
    list_filter = ["status", "staff_type", "organization", "campus"]
    search_fields = ["employee_number", "first_name", "last_name", "designation", "email"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    raw_id_fields = ["user"]
