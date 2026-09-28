from django.contrib import admin

from .models import SupportTicket, TicketComment


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ["subject", "organization", "campus", "status", "raised_by", "assigned_to"]
    list_filter = ["organization", "status"]
    search_fields = ["subject"]


@admin.register(TicketComment)
class TicketCommentAdmin(admin.ModelAdmin):
    list_display = ["ticket", "author", "created_at"]
    list_filter = ["organization"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
