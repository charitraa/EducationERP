from django.contrib import admin

from .models import Appointment, AppointmentSlot, Message, MessageThread


@admin.register(MessageThread)
class MessageThreadAdmin(admin.ModelAdmin):
    list_display = ["staff_user", "other_user", "campus", "is_closed", "last_message_at"]
    list_filter = ["organization", "is_closed"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["thread", "sender", "created_at"]
    list_filter = ["organization"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AppointmentSlot)
class AppointmentSlotAdmin(admin.ModelAdmin):
    list_display = ["staff", "campus", "starts_at", "ends_at", "is_cancelled"]
    list_filter = ["organization", "is_cancelled"]


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ["slot", "requested_by", "student", "status"]
    list_filter = ["organization", "status"]
