from rest_framework import serializers

from core.common.serializers import target_organization_id

from .models import Notice


class NoticeSerializer(serializers.ModelSerializer):
    campus_name = serializers.CharField(source="campus.name", read_only=True, default=None)
    is_published = serializers.BooleanField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notice
        fields = ["id", "organization", "campus", "campus_name", "audience", "title", "body", "published_at",
                  "expires_at", "is_published", "is_expired", "created_by", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "published_at", "created_by", "created_at", "updated_at"]

    def validate_campus(self, value):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown campus.")
        return value

    def validate(self, attrs):
        get = lambda k: attrs.get(k, getattr(self.instance, k, None) if self.instance else None)
        expires_at = get("expires_at")
        if expires_at and get("published_at") and expires_at < get("published_at"):
            raise serializers.ValidationError({"expires_at": "Must not be before it's published."})
        return attrs


class PublishNoticeSerializer(serializers.Serializer):
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
