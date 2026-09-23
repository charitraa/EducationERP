from django.contrib import admin

from .models import Parent, StudentParent


class StudentParentInline(admin.TabularInline):
    model = StudentParent
    extra = 0
    raw_id_fields = ["student"]
    fields = ["student", "relationship", "is_primary_contact"]


@admin.register(Parent)
class ParentAdmin(admin.ModelAdmin):
    list_display = ["first_name", "last_name", "phone", "email", "organization"]
    list_filter = ["organization"]
    search_fields = ["first_name", "last_name", "phone", "email"]
    readonly_fields = ["created_at", "updated_at", "deleted_at"]
    raw_id_fields = ["user"]
    inlines = [StudentParentInline]
