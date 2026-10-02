import ipaddress

from django.utils import timezone
from rest_framework import serializers

from core.organizations.models import Campus
from core.permissions.models import Role
from core.permissions.selectors import roles_available_to

from .models import ApiKey

RATE_PERIODS = ("s", "sec", "m", "min", "h", "hour", "d", "day")


class GrantSerializer(serializers.Serializer):
    """A role for the key, optionally at one campus, both of the key's
    organization (``context["organization_id"]``)."""

    role = serializers.PrimaryKeyRelatedField(queryset=Role.objects.all())
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all(), required=False, allow_null=True)

    def validate_role(self, value):
        if not roles_available_to(self.context["organization_id"]).filter(pk=value.pk).exists():
            raise serializers.ValidationError("Unknown role.")
        return value

    def validate_campus(self, value):
        if value is not None and value.organization_id != self.context["organization_id"]:
            raise serializers.ValidationError("Unknown campus.")
        return value


class RoleGrantSerializer(serializers.Serializer):
    role = serializers.IntegerField(source="role.pk")
    role_code = serializers.CharField(source="role.code")
    campus = serializers.IntegerField(source="campus_id", allow_null=True)


class ApiKeySerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    is_usable = serializers.SerializerMethodField()

    class Meta:
        model = ApiKey
        fields = ["id", "organization", "name", "description", "prefix", "read_only", "expires_at", "allowed_ips",
                  "rate_limit", "roles", "is_usable", "created_by", "created_at", "last_used_at", "last_used_ip",
                  "revoked_at", "revoked_by", "revoked_reason"]
        read_only_fields = ["id", "organization", "prefix", "roles", "is_usable", "created_by", "created_at",
                            "last_used_at", "last_used_ip", "revoked_at", "revoked_by", "revoked_reason"]

    def get_roles(self, obj) -> list[dict]:
        return RoleGrantSerializer(obj.user.role_assignments.select_related("role"), many=True).data

    def get_is_usable(self, obj) -> bool:
        return obj.revoked_at is None and (obj.expires_at is None or obj.expires_at > timezone.now())

    def validate_expires_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("Must be in the future.")
        return value

    def validate_allowed_ips(self, value):
        if not isinstance(value, list) or len(value) > 50:
            raise serializers.ValidationError("A list of up to 50 addresses or networks.")
        clean = []
        for item in value:
            try:
                clean.append(str(ipaddress.ip_network(str(item).strip(), strict=False)))
            except ValueError:
                raise serializers.ValidationError(f"Not an address or network: {item}.")
        return clean

    def validate_rate_limit(self, value):
        if not value:
            return ""
        num, _, period = value.partition("/")
        if not num.isdigit() or int(num) < 1 or period not in RATE_PERIODS:
            raise serializers.ValidationError('Like "1000/hour": a number, then sec, min, hour or day.')
        return value

    def validate(self, attrs):
        if self.instance is not None and self.instance.revoked_at is not None:
            raise serializers.ValidationError("A revoked key can't be changed.")
        return attrs


class CreateApiKeySerializer(ApiKeySerializer):
    grants = GrantSerializer(many=True, required=False, write_only=True,
                             help_text="Roles to give the key now; more can be added later.")

    class Meta(ApiKeySerializer.Meta):
        fields = ApiKeySerializer.Meta.fields + ["grants"]


class CreatedApiKeySerializer(ApiKeySerializer):
    key = serializers.CharField(read_only=True, help_text="Shown once. Store it now; it can't be shown again.")

    class Meta(ApiKeySerializer.Meta):
        fields = ApiKeySerializer.Meta.fields + ["key"]


class RevokeApiKeySerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
