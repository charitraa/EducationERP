from django.contrib import admin

from .models import (
    AcademicYear,
    Batch,
    CurriculumSubject,
    Department,
    Program,
    Room,
    Section,
    Subject,
    TeachingAssignment,
    Term,
)


class CurriculumInline(admin.TabularInline):
    model = CurriculumSubject
    extra = 0
    raw_id_fields = ["subject"]
    fields = ["level", "subject", "is_elective"]


class TermInline(admin.TabularInline):
    model = Term
    extra = 0
    fields = ["sequence", "name", "start_date", "end_date"]


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "head", "organization"]
    list_filter = ["organization"]
    search_fields = ["code", "name"]
    raw_id_fields = ["head"]


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "level_type", "first_level", "last_level", "is_active"]
    list_filter = ["organization", "level_type", "is_active"]
    search_fields = ["code", "name"]
    inlines = [CurriculumInline]


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "department", "credit_hours"]
    list_filter = ["organization"]
    search_fields = ["code", "name"]


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ["name", "start_date", "end_date", "is_current", "organization"]
    list_filter = ["organization", "is_current"]
    inlines = [TermInline]


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "campus", "room_type", "capacity", "is_active"]
    list_filter = ["organization", "campus", "room_type"]
    search_fields = ["code", "name"]


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "program", "campus", "start_year", "is_active"]
    list_filter = ["organization", "program", "campus"]
    search_fields = ["code", "name"]


class TeachingAssignmentInline(admin.TabularInline):
    model = TeachingAssignment
    extra = 0
    raw_id_fields = ["subject", "teacher"]


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ["__str__", "campus", "class_teacher", "capacity"]
    list_filter = ["organization", "academic_year", "campus", "program"]
    raw_id_fields = ["class_teacher", "home_room", "batch"]
    inlines = [TeachingAssignmentInline]
