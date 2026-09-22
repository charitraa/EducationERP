from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets

from core.common.mixins import OrganizationScopedMixin
from core.common.permissions import HasPermission

from .filters import AuditLogFilter
from .models import AuditLog
from .serializers import AuditLogSerializer


@extend_schema_view(
    list=extend_schema(tags=["audit"], summary="List audit log entries"),
    retrieve=extend_schema(tags=["audit"], summary="Retrieve an audit log entry"),
)
class AuditLogViewSet(OrganizationScopedMixin, viewsets.ReadOnlyModelViewSet):
    """The audit trail, scoped to the caller's organization.

    Read-only over HTTP with no write endpoints at all — entries are created
    by the system, never by a client.
    """

    queryset = AuditLog.objects.select_related("actor", "organization")
    serializer_class = AuditLogSerializer
    permission_classes = [HasPermission]
    required_permissions = {"default": ["audit.view"]}
    filterset_class = AuditLogFilter
    search_fields = ["object_repr", "actor_email", "object_type"]
    ordering_fields = ["created_at", "action", "module"]
    ordering = ["-created_at"]
