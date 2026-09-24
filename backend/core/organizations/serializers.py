from rest_framework import serializers

from .models import Campus, Organization


class LowercaseCodeMixin:
    """Codes are stored lower-case. Normalise the input before the model's
    validator (lower-case only) sees it, so "KMC" is accepted as "kmc"
    instead of refused."""

    def to_internal_value(self, data):
        code = data.get("code") if hasattr(data, "get") else None
        if isinstance(code, str):
            data = data.copy()
            data["code"] = code.strip().lower()
        return super().to_internal_value(data)


class OrganizationSerializer(LowercaseCodeMixin, serializers.ModelSerializer):
    campus_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Organization
        fields = [
            "id", "name", "code", "legal_name", "type",
            "email", "phone", "website", "address", "timezone",
            "is_active", "campus_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "campus_count"]


class OrganizationWriteSerializer(OrganizationSerializer):
    """Code is immutable once set — other records reference it."""

    class Meta(OrganizationSerializer.Meta):
        pass

    def update(self, instance, validated_data):
        validated_data.pop("code", None)
        return super().update(instance, validated_data)


class CampusSerializer(LowercaseCodeMixin, serializers.ModelSerializer):
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

    def validate(self, attrs):
        """Campus codes, and the main campus, are unique within an organization."""
        if self.instance is not None:
            organization_id = self.instance.organization_id
        else:
            organization_id = self.context["view"].get_target_organization_id()

        if attrs.get("is_main"):
            other_main = Campus.objects.filter(organization_id=organization_id, is_main=True)
            if self.instance is not None:
                other_main = other_main.exclude(pk=self.instance.pk)
            if other_main.exists():
                raise serializers.ValidationError(
                    {"is_main": "This organization already has a main campus. Unset it first."}
                )

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
