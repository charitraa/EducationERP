from django.contrib import admin

from .models import Admission


@admin.register(Admission)
class AdmissionAdmin(admin.ModelAdmin):
    list_display = ["application_number", "first_name", "last_name", "campus", "status", "applied_on"]
    list_filter = ["status", "organization", "campus"]
    search_fields = ["application_number", "first_name", "last_name", "email", "phone"]
    # The workflow runs through the API's services; the admin only reads it.
    readonly_fields = [
        "status", "decided_at", "decided_by", "decision_note", "student",
        "created_at", "updated_at", "deleted_at",
    ]
