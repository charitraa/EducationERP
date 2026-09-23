from django.contrib import admin

from .models import Enrollment, Student


class EnrollmentInline(admin.TabularInline):
    model = Enrollment
    extra = 0
    # History is written by the services; editing it here would break the
    # one-open-enrollment invariant they maintain.
    readonly_fields = ["campus", "status", "started_on", "ended_on", "end_reason", "created_at"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ["student_number", "first_name", "last_name", "campus", "status", "organization"]
    list_filter = ["status", "organization", "campus"]
    search_fields = ["student_number", "first_name", "last_name", "email"]
    readonly_fields = ["status", "campus", "created_at", "updated_at", "deleted_at"]
    raw_id_fields = ["user"]
    inlines = [EnrollmentInline]

    def has_add_permission(self, request):
        # Creating here would skip create_student(), leaving a student with no
        # enrollment. Students are created through the API or an admission.
        return False
