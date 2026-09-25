from django.contrib import admin

from .models import (
    AttendanceCorrection,
    AttendanceDevice,
    AttendanceRecord,
    AttendanceSession,
    BiometricIdentity,
    Punch,
    StaffAttendanceDay,
    WorkSchedule,
)


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ["date", "section", "kind", "teacher", "status", "organization"]
    list_filter = ["organization", "kind", "status"]
    date_hierarchy = "date"


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ["session", "enrollment", "status", "source", "marked_by"]
    list_filter = ["organization", "status", "source"]


@admin.register(AttendanceCorrection)
class AttendanceCorrectionAdmin(admin.ModelAdmin):
    list_display = ["record", "old_status", "new_status", "corrected_by", "corrected_at"]

    def has_change_permission(self, request, obj=None):
        return False  # history


@admin.register(WorkSchedule)
class WorkScheduleAdmin(admin.ModelAdmin):
    list_display = ["name", "campus", "start_time", "end_time", "is_default", "organization"]


@admin.register(AttendanceDevice)
class AttendanceDeviceAdmin(admin.ModelAdmin):
    list_display = ["name", "serial_number", "kind", "campus", "is_active", "last_seen_at"]
    exclude = ["key_hash"]


@admin.register(BiometricIdentity)
class BiometricIdentityAdmin(admin.ModelAdmin):
    list_display = ["pin", "staff", "student", "organization"]


@admin.register(Punch)
class PunchAdmin(admin.ModelAdmin):
    list_display = ["punched_at", "pin", "staff", "student", "source", "device"]
    list_filter = ["organization", "source"]

    def has_change_permission(self, request, obj=None):
        return False  # raw events are never edited


@admin.register(StaffAttendanceDay)
class StaffAttendanceDayAdmin(admin.ModelAdmin):
    list_display = ["date", "staff", "status", "first_in", "last_out", "is_override"]
    list_filter = ["organization", "status", "is_override"]
