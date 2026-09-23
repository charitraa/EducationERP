from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets

from core.common.mixins import BaseModelViewSet
from core.common.permissions import HasPermission, IsSameOrganization

from .models import Permission, Role
from .selectors import organization_wide_permission_codes, visible_roles_for
from .serializers import PermissionSerializer, RoleSerializer


@extend_schema_view(
    list=extend_schema(tags=["permissions"], summary="List available permissions"),
    retrieve=extend_schema(tags=["permissions"], summary="Retrieve a permission"),
)
class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """The permission catalogue.

    Read-only by design: permissions are declared in code
    (``core/permissions/registry.py``) and synced with ``sync_permissions``,
    so the set of capabilities can never drift from what the code enforces.
    """

    queryset = Permission.objects.all()
    serializer_class = PermissionSerializer
    permission_classes = [HasPermission]
    required_permissions = {"default": ["permissions.view"]}
    filterset_fields = ["module"]
    search_fields = ["code", "name", "module"]
    ordering_fields = ["module", "code"]
    pagination_class = None


@extend_schema_view(
    list=extend_schema(tags=["permissions"], summary="List roles"),
    retrieve=extend_schema(tags=["permissions"], summary="Retrieve a role"),
    create=extend_schema(tags=["permissions"], summary="Create a role"),
    update=extend_schema(tags=["permissions"], summary="Replace a role"),
    partial_update=extend_schema(tags=["permissions"], summary="Update a role"),
    destroy=extend_schema(tags=["permissions"], summary="Soft-delete a role"),
)
class RoleViewSet(BaseModelViewSet):
    """Roles bundle permissions; users receive roles, never raw permissions.

    Callers see system roles plus their own organization's roles, and can only
    modify the latter.
    """

    queryset = Role.objects.select_related("organization").prefetch_related("permissions")
    serializer_class = RoleSerializer
    permission_classes = [HasPermission, IsSameOrganization]
    audit_module = "rbac"
    filterset_fields = ["is_system"]
    search_fields = ["code", "name"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name", "pk"]

    required_permissions = {
        "list": ["roles.view"],
        "retrieve": ["roles.view"],
        "create": ["roles.create"],
        "update": ["roles.update"],
        "partial_update": ["roles.update"],
        "destroy": ["roles.delete"],
    }

    def get_queryset(self):
        # See OrganizationViewSet: annotate() drops Meta.ordering.
        return (
            visible_roles_for(self.request.user)
            .annotate(assigned_user_count=Count("assignments", distinct=True))
            .order_by("name", "pk")
        )

    def get_create_kwargs(self):
        """New roles always belong to the creator's organization."""
        kwargs = super().get_create_kwargs()
        user = self.request.user
        if user.organization_id:
            kwargs["organization_id"] = user.organization_id
        kwargs["is_system"] = False
        return kwargs

    def perform_destroy(self, instance):
        from rest_framework.exceptions import PermissionDenied

        if instance.is_system:
            raise PermissionDenied("System roles cannot be deleted.")
        if not self.request.user.is_superuser:
            held = organization_wide_permission_codes(self.request.user)
            if set(instance.permissions.values_list("code", flat=True)) - held:
                raise PermissionDenied(
                    "You can only delete roles whose permissions you hold yourself."
                )
        super().perform_destroy(instance)
