from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "event_type", "title", "body", "data", "is_read", "read_at", "created_at"]
        read_only_fields = fields
