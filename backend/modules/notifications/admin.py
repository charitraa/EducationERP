from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["recipient", "event_type", "title", "is_read", "created_at"]
    list_filter = ["organization", "event_type", "is_read"]
    search_fields = ["title", "recipient__email"]

    def has_add_permission(self, request):
        return False
