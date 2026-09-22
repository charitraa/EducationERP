from rest_framework import serializers

from .models import Permission, Role, UserRole


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "code", "module", "action", "name", "description"]
        read_only_fields = fields


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.SlugRelatedField(
        slug_field="code",
        many=True,
        queryset=Permission.objects.all(),
        required=False,
    )
    organization_name = serializers.CharField(
        source="organization.name", read_only=True, default=None
    )
    assigned_user_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Role
        fields = [
            "id", "organization", "organization_name", "code", "name",
            "description", "is_system", "permissions", "assigned_user_count",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "organization", "is_system", "assigned_user_count",
            "created_at", "updated_at",
        ]

    def validate_code(self, value):
        return value.lower()

    def validate(self, attrs):
        if self.instance is not None and self.instance.is_system:
            raise serializers.ValidationError(
                "System roles are managed by the platform and cannot be modified."
            )

        code = attrs.get("code", getattr(self.instance, "code", None))
        request = self.context["request"]
        organization_id = (
            self.instance.organization_id
            if self.instance is not None
            else request.user.organization_id
        )
        if code and organization_id:
            clash = Role.objects.filter(organization_id=organization_id, code=code)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {"code": "A role with this code already exists in this organization."}
                )
        return attrs


class UserRoleSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source="user.email", read_only=True)
    role_code = serializers.CharField(source="role.code", read_only=True)
    campus_name = serializers.CharField(
        source="campus.name", read_only=True, default=None
    )

    class Meta:
        model = UserRole
        fields = [
            "id", "user", "user_email", "role", "role_code",
            "campus", "campus_name", "granted_by", "expires_at", "created_at",
        ]
        read_only_fields = fields
