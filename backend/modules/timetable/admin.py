from django.contrib import admin

from .models import BellSchedule, LessonChange, Period, TimetableEntry


class PeriodInline(admin.TabularInline):
    model = Period
    extra = 0
    fields = ["name", "start_time", "end_time", "is_break"]


@admin.register(BellSchedule)
class BellScheduleAdmin(admin.ModelAdmin):
    list_display = ["name", "campus", "is_active", "organization"]
    list_filter = ["organization", "campus", "is_active"]
    search_fields = ["name"]
    inlines = [PeriodInline]


@admin.register(TimetableEntry)
class TimetableEntryAdmin(admin.ModelAdmin):
    list_display = ["__str__", "day_of_week", "period", "room", "term"]
    list_filter = ["organization", "day_of_week", "period__schedule__campus"]
    raw_id_fields = ["teaching_assignment", "period", "room", "term"]


@admin.register(LessonChange)
class LessonChangeAdmin(admin.ModelAdmin):
    list_display = ["date", "entry", "is_cancelled", "substitute_teacher", "room"]
    list_filter = ["organization", "is_cancelled"]
    date_hierarchy = "date"
    raw_id_fields = ["entry", "substitute_teacher", "room"]
