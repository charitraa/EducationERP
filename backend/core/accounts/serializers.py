from django.contrib.auth.password_validation import validate_password
from drf_spectacular.utils import extend_schema_field
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.organizations.models import Campus
from core.permissions.models import Role, UserRole

from .models import User


class RoleAssignmentSerializer(serializers.ModelSerializer):
    """Nested, read-only view of a user's role assignments."""

    role_code = serializers.CharField(source="role.code", read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True)
    campus_name = serializers.CharField(source="campus.name", read_only=True, default=None)

    class Meta:
        model = UserRole
        fields = [
            "id", "role", "role_code", "role_name",
            "campus", "campus_name", "expires_at", "created_at",
        ]
        read_only_fields = fields


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    role_assignments = RoleAssignmentSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "email", "phone", "first_name", "middle_name", "last_name",
            "full_name", "organization", "user_type", "is_active", "is_staff",
            "date_joined", "last_login", "role_assignments",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "organization", "is_staff", "date_joined", "last_login",
            "role_assignments", "created_at", "updated_at",
        ]


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    role_codes = serializers.ListField(
        child=serializers.CharField(),
        write_only=True,
        required=False,
        help_text="Role codes to assign on creation, e.g. ['staff'].",
    )

    class Meta:
        model = User
        fields = [
            "id", "email", "phone", "password", "first_name", "middle_name",
            "last_name", "user_type", "is_active", "role_codes",
        ]
        read_only_fields = ["id"]

    def validate_email(self, value):
        value = value.lower().strip()
        if User.all_objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value

    def validate_role_codes(self, value):
        """Only system roles or roles from the caller's own organization."""
        from core.permissions.selectors import roles_available_to

        request = self.context["request"]
        found = roles_available_to(request.user.organization_id).filter(code__in=value)
        found_codes = set(found.values_list("code", flat=True))
        missing = set(value) - found_codes
        if missing:
            raise serializers.ValidationError(
                f"Unknown or inaccessible role codes: {', '.join(sorted(missing))}."
            )
        return value

    def create(self, validated_data):
        from .services import create_user

        role_codes = validated_data.pop("role_codes", [])
        password = validated_data.pop("password")
        return create_user(
            password=password,
            role_codes=role_codes,
            created_by=self.context["request"].user,
            **validated_data,
        )

    def to_representation(self, instance):
        return UserSerializer(instance, context=self.context).data


class CurrentUserSerializer(serializers.ModelSerializer):
    """``/auth/me/`` — identity plus everything a client needs to render a UI."""

    full_name = serializers.CharField(read_only=True)
    organization = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "phone", "first_name", "middle_name", "last_name",
            "full_name", "user_type", "is_active", "is_superuser",
            "organization", "roles", "permissions", "last_login", "date_joined",
        ]
        read_only_fields = fields

    @extend_schema_field(
        {"type": "object", "nullable": True,
         "properties": {"id": {"type": "integer"}, "name": {"type": "string"},
                        "code": {"type": "string"}}}
    )
    def get_organization(self, obj):
        if not obj.organization_id:
            return None
        return {
            "id": obj.organization.id,
            "name": obj.organization.name,
            "code": obj.organization.code,
        }

    @extend_schema_field(
        {"type": "array", "items": {"type": "object", "properties": {
            "code": {"type": "string"}, "name": {"type": "string"},
            "campus": {"type": "string", "nullable": True}}}}
    )
    def get_roles(self, obj):
        return [
            {
                "code": assignment.role.code,
                "name": assignment.role.name,
                "campus": assignment.campus.name if assignment.campus_id else None,
            }
            for assignment in obj.role_assignments.select_related("role", "campus")
        ]

    @extend_schema_field({"type": "array", "items": {"type": "string"}})
    def get_permissions(self, obj):
        return sorted(obj.get_permission_codes())


class CurrentUserUpdateSerializer(serializers.ModelSerializer):
    """A user may edit their own contact details — nothing security-relevant."""

    class Meta:
        model = User
        fields = ["first_name", "middle_name", "last_name", "phone"]


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        write_only=True, style={"input_type": "password"}
    )
    new_password = serializers.CharField(
        write_only=True, style={"input_type": "password"}
    )

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        user = self.context["request"].user
        try:
            validate_password(value, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value

    def validate(self, attrs):
        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError(
                {"new_password": "New password must differ from the current one."}
            )
        return attrs


class SetPasswordSerializer(serializers.Serializer):
    """Administrative password reset for another user."""

    new_password = serializers.CharField(
        write_only=True, style={"input_type": "password"}
    )

    def validate_new_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value


class AssignRoleSerializer(serializers.Serializer):
    """Role grant/revoke payload.

    Both querysets are narrowed to the caller's organization in ``__init__``,
    so a valid-looking id from another tenant fails validation rather than
    silently crossing the tenant boundary.
    """

    role = serializers.PrimaryKeyRelatedField(queryset=Role.objects.none())
    campus = serializers.PrimaryKeyRelatedField(
        queryset=Campus.objects.none(), required=False, allow_null=True
    )
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        organization_id = getattr(user, "organization_id", None)

        if user is not None and user.is_platform_admin:
            self.fields["role"].queryset = Role.objects.all()
            self.fields["campus"].queryset = Campus.objects.all()
        else:
            from core.permissions.selectors import roles_available_to

            self.fields["role"].queryset = roles_available_to(organization_id)
            self.fields["campus"].queryset = Campus.objects.filter(
                organization_id=organization_id
            )
