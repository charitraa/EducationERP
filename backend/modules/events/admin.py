from django.contrib import admin

from .models import (
    Award,
    AwardRule,
    Event,
    EventAttendance,
    EventCategory,
    EventParticipation,
    EventRegistration,
    PointEntry,
    PointRule,
    StudentAward,
    StudentPoints,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Attendance, participation and points are records; changes go through
    the API so they are checked and, for points, kept in step with the
    running total."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EventCategory)
class EventCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "organization", "is_active"]
    list_filter = ["organization"]


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["name", "campus", "category", "status", "start_at", "organized_by"]
    list_filter = ["organization", "status", "registration_mode"]
    search_fields = ["name"]


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ["event", "student", "status", "decided_at"]
    list_filter = ["organization", "status"]

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PointRule)
class PointRuleAdmin(admin.ModelAdmin):
    list_display = ["name", "source", "category", "points", "organization", "is_active"]
    list_filter = ["organization"]


@admin.register(Award)
class AwardAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "code", "organization", "is_active"]
    list_filter = ["organization", "kind"]


@admin.register(AwardRule)
class AwardRuleAdmin(admin.ModelAdmin):
    list_display = ["award", "threshold_kind", "threshold_value", "category", "organization"]
    list_filter = ["organization"]


admin.site.register(EventAttendance, ReadOnlyAdmin)
admin.site.register(EventParticipation, ReadOnlyAdmin)
admin.site.register(PointEntry, ReadOnlyAdmin)
admin.site.register(StudentPoints, ReadOnlyAdmin)
admin.site.register(StudentAward, ReadOnlyAdmin)
