from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema, extend_schema_view

from core.common.mixins import BaseModelViewSet, CampusScopedViewSet
from core.common.permissions import HasPermission, IsPlatformAdmin, IsSameOrganization

from .models import Campus, Organization
from .serializers import (
    CampusSerializer,
    OrganizationSerializer,
    OrganizationWriteSerializer,
)


@extend_schema_view(
    list=extend_schema(tags=["organizations"], summary="List organizations"),
    retrieve=extend_schema(tags=["organizations"], summary="Retrieve an organization"),
    create=extend_schema(tags=["organizations"], summary="Create an organization"),
    update=extend_schema(tags=["organizations"], summary="Replace an organization"),
    partial_update=extend_schema(tags=["organizations"], summary="Update an organization"),
    destroy=extend_schema(tags=["organizations"], summary="Soft-delete an organization"),
)
class OrganizationViewSet(BaseModelViewSet):
    """Tenants on the platform.

    Non-superusers only ever see their own organization; creating and deleting
    tenants is a platform-level action.
    """

    # annotate() introduces a GROUP BY, which discards Meta.ordering and makes
    # pagination non-deterministic — so ordering is restated explicitly.
    queryset = Organization.objects.annotate(
        campus_count=Count("campuses", filter=Q(campuses__deleted_at__isnull=True))
    ).order_by("name", "pk")
    serializer_class = OrganizationSerializer
    permission_classes = [HasPermission, IsSameOrganization]
    audit_module = "organizations"
    filterset_fields = ["type", "is_active"]
    search_fields = ["name", "code", "legal_name"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name", "pk"]

    required_permissions = {
        "list": ["organizations.view"],
        "retrieve": ["organizations.view"],
        "update": ["organizations.update"],
        "partial_update": ["organizations.update"],
        # create and destroy: platform superusers only, see get_permissions.
    }

    def get_permissions(self):
        # Onboarding and removing a tenant is a platform decision. Without
        # this, org-admin (which holds every permission) could create stray
        # organizations it can't even see, or delete its own and lock every
        # user out.
        if self.action in ("create", "destroy"):
            return [IsPlatformAdmin()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return OrganizationWriteSerializer
        return OrganizationSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_platform_admin:
            return qs
        if user.organization_id is None:
            return qs.none()
        return qs.filter(pk=user.organization_id)


@extend_schema_view(
    list=extend_schema(tags=["organizations"], summary="List campuses"),
    retrieve=extend_schema(tags=["organizations"], summary="Retrieve a campus"),
    create=extend_schema(tags=["organizations"], summary="Create a campus"),
    update=extend_schema(tags=["organizations"], summary="Replace a campus"),
    partial_update=extend_schema(tags=["organizations"], summary="Update a campus"),
    destroy=extend_schema(tags=["organizations"], summary="Soft-delete a campus"),
)
class CampusViewSet(CampusScopedViewSet):
    """Physical locations within an organization.

    A role scoped to one campus reaches only that campus here, like any other
    campus-owned record; the campus is its own scope.
    """

    queryset = Campus.objects.select_related("organization")
    campus_field = "pk"
    serializer_class = CampusSerializer
    audit_module = "organizations"
    filterset_fields = ["is_active", "is_main", "city"]
    search_fields = ["name", "code", "city"]
    ordering_fields = ["name", "created_at"]

    required_permissions = {
        "list": ["campuses.view"],
        "retrieve": ["campuses.view"],
        "create": ["campuses.create"],
        "update": ["campuses.update"],
        "partial_update": ["campuses.update"],
        "destroy": ["campuses.delete"],
    }

    def perform_create(self, serializer):
        # A new campus is outside every campus scope, so only an
        # organization-wide grant can create one.
        from rest_framework.exceptions import PermissionDenied

        from core.permissions.selectors import campus_ids_with_permission

        if campus_ids_with_permission(self.request.user, "campuses.create") is not None:
            raise PermissionDenied("Creating a campus needs an organization-wide role.")
        super().perform_create(serializer)
