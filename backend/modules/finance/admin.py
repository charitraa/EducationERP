from django.contrib import admin

from .models import (
    FeeCategory,
    FeeStructure,
    FeeStructureItem,
    Installment,
    Invoice,
    InvoiceItem,
    Payment,
    Receipt,
    Refund,
    Scholarship,
    StudentScholarship,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Money that's moved is a record; changes go through the API so they
    are checked, reversed properly and audited."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FeeCategory)
class FeeCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "organization", "is_active"]
    list_filter = ["organization"]


class FeeStructureItemInline(admin.TabularInline):
    model = FeeStructureItem
    extra = 0


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = ["__str__", "program", "level", "academic_year", "organization"]
    list_filter = ["organization"]
    inlines = [FeeStructureItemInline]


@admin.register(Scholarship)
class ScholarshipAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "value", "organization", "is_active"]
    list_filter = ["organization"]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ["invoice_number", "student", "term", "status", "total", "paid_amount", "due_date"]
    list_filter = ["organization", "status"]
    search_fields = ["invoice_number"]

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(StudentScholarship, ReadOnlyAdmin)
admin.site.register(InvoiceItem, ReadOnlyAdmin)
admin.site.register(Installment, ReadOnlyAdmin)
admin.site.register(Payment, ReadOnlyAdmin)
admin.site.register(Receipt, ReadOnlyAdmin)
admin.site.register(Refund, ReadOnlyAdmin)
