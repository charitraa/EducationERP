from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id", "actor", "actor_email", "actor_name", "organization",
            "action", "module", "object_type", "object_id", "object_repr",
            "changes", "metadata", "ip_address", "user_agent",
            "request_path", "request_method", "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field({"type": "string", "nullable": True})
    def get_actor_name(self, obj):
        return obj.actor.get_full_name() if obj.actor_id else None
