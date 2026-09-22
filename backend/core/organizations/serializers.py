from rest_framework import serializers

from .models import Campus, Organization


class OrganizationSerializer(serializers.ModelSerializer):
    campus_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Organization
        fields = [
            "id", "name", "code", "legal_name", "type",
            "email", "phone", "website", "address", "timezone",
            "is_active", "campus_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "campus_count"]

    def validate_code(self, value):
        return value.lower()


class OrganizationWriteSerializer(OrganizationSerializer):
    """Code is immutable once set — other records reference it."""

    class Meta(OrganizationSerializer.Meta):
        pass

    def update(self, instance, validated_data):
        validated_data.pop("code", None)
        return super().update(instance, validated_data)


class CampusSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)

    class Meta:
        model = Campus
        fields = [
            "id", "organization", "organization_name", "name", "code",
            "email", "phone", "address", "city", "state", "country",
            "is_main", "is_active", "created_at", "updated_at",
        ]
        # Tenant is taken from the authenticated user, never the payload.
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        return value.lower()

    def validate(self, attrs):
        """Campus codes are unique within an organization."""
        organization = getattr(self.instance, "organization", None)
        if organization is None:
            user = self.context["request"].user
            organization_id = user.organization_id
        else:
            organization_id = organization.pk

        code = attrs.get("code", getattr(self.instance, "code", None))
        if code and organization_id:
            clash = Campus.objects.filter(organization_id=organization_id, code=code)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {"code": "A campus with this code already exists in this organization."}
                )
        return attrs
