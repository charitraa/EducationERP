from django.contrib import admin

from .models import (
    AdmitCard,
    Exam,
    ExamRoom,
    ExamSubject,
    ExamType,
    GradeScale,
    Invigilation,
    Mark,
    MarkCorrection,
    MarkSheet,
    Result,
    ResultPlan,
    SeatAllocation,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Marks, sheets and results are records; changes go through the API so
    they are checked, corrected and audited."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GradeScale)
class GradeScaleAdmin(admin.ModelAdmin):
    list_display = ["name", "program", "organization"]
    list_filter = ["organization"]


@admin.register(ExamType)
class ExamTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "organization", "is_active"]
    list_filter = ["organization"]


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ["name", "campus", "program", "academic_year", "status", "start_date", "organization"]
    list_filter = ["organization", "status"]
    search_fields = ["name"]


@admin.register(ExamSubject)
class ExamSubjectAdmin(admin.ModelAdmin):
    list_display = ["exam", "subject", "level", "date", "start_time", "end_time"]
    list_filter = ["organization"]


admin.site.register(ExamRoom, ReadOnlyAdmin)
admin.site.register(SeatAllocation, ReadOnlyAdmin)
admin.site.register(Invigilation, ReadOnlyAdmin)
admin.site.register(AdmitCard, ReadOnlyAdmin)
admin.site.register(MarkSheet, ReadOnlyAdmin)
admin.site.register(Mark, ReadOnlyAdmin)
admin.site.register(MarkCorrection, ReadOnlyAdmin)
admin.site.register(ResultPlan, ReadOnlyAdmin)
admin.site.register(Result, ReadOnlyAdmin)
