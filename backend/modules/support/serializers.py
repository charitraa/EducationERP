from rest_framework import serializers

from core.accounts.models import User
from core.common.serializers import target_organization_id

from .models import SupportTicket, TicketComment


class TicketCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.get_full_name", read_only=True, default="")

    class Meta:
        model = TicketComment
        fields = ["id", "ticket", "author", "author_name", "body", "created_at"]
        read_only_fields = fields


class AddCommentSerializer(serializers.Serializer):
    body = serializers.CharField()


class SupportTicketSerializer(serializers.ModelSerializer):
    raised_by_name = serializers.CharField(source="raised_by.get_full_name", read_only=True, default="")
    assigned_to_name = serializers.CharField(source="assigned_to.get_full_name", read_only=True, default=None)
    campus_name = serializers.CharField(source="campus.name", read_only=True, default=None)

    class Meta:
        model = SupportTicket
        fields = ["id", "organization", "campus", "campus_name", "raised_by", "raised_by_name", "subject",
                  "description", "status", "assigned_to", "assigned_to_name", "resolved_at", "closed_at",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "campus", "campus_name", "raised_by", "raised_by_name",
                           "status", "assigned_to", "assigned_to_name", "resolved_at", "closed_at",
                           "created_at", "updated_at"]


class AssignTicketSerializer(serializers.Serializer):
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    def validate_assigned_to(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown user.")
        return value
