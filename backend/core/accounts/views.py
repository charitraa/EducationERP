from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from core.common.mixins import OrganizationScopedViewSet
from core.permissions.models import UserRole

from .models import User
from .serializers import (
    AssignRoleSerializer,
    RoleAssignmentSerializer,
    SetPasswordSerializer,
    UserCreateSerializer,
    UserSerializer,
)
from .services import assign_role, change_password, ensure_can_manage_user, revoke_role


@extend_schema_view(
    list=extend_schema(tags=["users"], summary="List users"),
    retrieve=extend_schema(tags=["users"], summary="Retrieve a user"),
    create=extend_schema(tags=["users"], summary="Create a user"),
    update=extend_schema(tags=["users"], summary="Replace a user"),
    partial_update=extend_schema(tags=["users"], summary="Update a user"),
    destroy=extend_schema(tags=["users"], summary="Soft-delete a user"),
)
class UserViewSet(OrganizationScopedViewSet):
    """User accounts within an organization.

    Students, parents, teachers and staff are all users here; the profile data
    specific to each arrives with the Phase 2 modules.
    """

    queryset = User.objects.select_related("organization").prefetch_related(
        Prefetch(
            "role_assignments",
            queryset=UserRole.objects.select_related("role", "campus"),
        )
    )
    serializer_class = UserSerializer
    audit_module = "accounts"
    filterset_fields = ["user_type", "is_active"]
    search_fields = ["email", "first_name", "last_name", "phone"]
    ordering_fields = ["email", "date_joined", "created_at"]

    required_permissions = {
        "list": ["users.view"],
        "retrieve": ["users.view"],
        "create": ["users.create"],
        "update": ["users.update"],
        "partial_update": ["users.update"],
        "destroy": ["users.delete"],
        "roles": ["users.manage_roles"],
        "assign_role": ["users.manage_roles"],
        "revoke_role": ["users.manage_roles"],
        "set_password": ["users.update"],
        "deactivate": ["users.update"],
        "reset_two_factor": ["users.update"],
    }

    # Actions that change the target account or its access. Each needs the
    # target to be no more powerful than the caller — see ensure_can_manage_user.
    managing_actions = {
        "update", "partial_update", "destroy",
        "assign_role", "revoke_role", "set_password", "deactivate",
        "reset_two_factor",
    }

    def get_object(self):
        user = super().get_object()
        if self.action in self.managing_actions:
            ensure_can_manage_user(self.request.user, user)
        return user

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        if self.action in ("assign_role", "revoke_role"):
            return AssignRoleSerializer
        if self.action == "set_password":
            return SetPasswordSerializer
        return UserSerializer

    @extend_schema(tags=["users"], summary="List a user's role assignments")
    @action(detail=True, methods=["get"])
    def roles(self, request, pk=None):
        user = self.get_object()
        assignments = user.role_assignments.select_related("role", "campus")
        return Response(RoleAssignmentSerializer(assignments, many=True).data)

    @extend_schema(
        tags=["users"],
        summary="Assign a role to a user",
        request=AssignRoleSerializer,
        responses={201: RoleAssignmentSerializer},
    )
    @action(detail=True, methods=["post"], url_path="assign-role")
    def assign_role(self, request, pk=None):
        user = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        assignment = assign_role(
            user=user,
            role=serializer.validated_data["role"],
            campus=serializer.validated_data.get("campus"),
            expires_at=serializer.validated_data.get("expires_at"),
            granted_by=request.user,
        )
        return Response(
            RoleAssignmentSerializer(assignment).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        tags=["users"],
        summary="Revoke a role from a user",
        request=AssignRoleSerializer,
        responses={204: None},
    )
    @action(detail=True, methods=["post"], url_path="revoke-role")
    def revoke_role(self, request, pk=None):
        user = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        assignment = UserRole.objects.filter(
            user=user,
            role=serializer.validated_data["role"],
            campus=serializer.validated_data.get("campus"),
        ).first()
        if assignment is None:
            return Response(
                {"error": {"code": "not_assigned", "message": "Role is not assigned to this user.", "details": None}},
                status=status.HTTP_404_NOT_FOUND,
            )

        revoke_role(assignment=assignment, revoked_by=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=["users"],
        summary="Set a user's password (administrative)",
        request=SetPasswordSerializer,
        responses={204: None},
    )
    @action(detail=True, methods=["post"], url_path="set-password")
    def set_password(self, request, pk=None):
        user = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_password(
            user=user,
            new_password=serializer.validated_data["new_password"],
            changed_by=request.user,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=["users"], summary="Deactivate a user", responses={200: UserSerializer})
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        from .services import deactivate_user

        user = deactivate_user(user=self.get_object(), deactivated_by=request.user)
        return Response(UserSerializer(user, context=self.get_serializer_context()).data)

    @extend_schema(
        tags=["users"],
        summary="Turn off a user's two-factor login (e.g. lost phone)",
        request=None,
        responses={204: None},
    )
    @action(detail=True, methods=["post"], url_path="reset-2fa")
    def reset_two_factor(self, request, pk=None):
        from core.authentication.two_factor import disable

        disable(self.get_object(), by=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
