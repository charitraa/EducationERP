from django.contrib import admin

from .models import Notice


@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ["title", "organization", "campus", "audience", "published_at", "expires_at"]
    list_filter = ["organization", "audience"]
    search_fields = ["title"]
