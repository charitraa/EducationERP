from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.mixins import OrganizationScopedViewSet
from core.common.permissions import IsSameOrganization
from core.permissions.selectors import campus_ids_with_permission

from . import selectors, services
from .models import Notice
from .serializers import NoticeSerializer, PublishNoticeSerializer

TAG = "notices"
MANAGE = "notices.manage"


def _schema(*actions):
    summaries = {"list": "List notices", "retrieve": "Retrieve a notice", "create": "Create a notice",
                "update": "Replace a notice", "partial_update": "Update a notice", "destroy": "Delete a notice"}
    return extend_schema_view(**{a: extend_schema(tags=[TAG], summary=summaries[a]) for a in actions})


@_schema("list", "retrieve", "create", "update", "partial_update", "destroy")
class NoticeViewSet(OrganizationScopedViewSet):
    queryset = Notice.objects.select_related("campus", "created_by")
    serializer_class = NoticeSerializer
    audit_module = "notices"
    filterset_fields = ["campus", "audience"]
    search_fields = ["title"]
    required_permissions = {"create": [MANAGE], "update": [MANAGE], "partial_update": [MANAGE],
                            "destroy": [MANAGE], "publish": [MANAGE]}

    def get_permissions(self):
        # Reading is open to every organization member — a notice's audience
        # and campus decide what they see, not a permission code.
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), IsSameOrganization()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action not in ("list", "retrieve"):
            return qs
        user = self.request.user
        if user.is_platform_admin:
            return qs
        campus_ids = campus_ids_with_permission(user, MANAGE)
        if campus_ids is None:
            return qs  # org-wide notices.manage: every notice, drafts included
        if campus_ids:
            return qs.filter(campus_id__in=campus_ids) | selectors.visible_to(user)
        return selectors.visible_to(user)

    def get_create_kwargs(self) -> dict:
        kwargs = super().get_create_kwargs()
        kwargs["created_by"] = self.request.user
        return kwargs

    @extend_schema(tags=[TAG], summary="Publish the notice", request=PublishNoticeSerializer,
                   responses={200: NoticeSerializer})
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        notice = self.get_object()
        serializer = PublishNoticeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        notice = services.publish_notice(notice, expires_at=serializer.validated_data.get("expires_at"),
                                         by=request.user)
        return Response(NoticeSerializer(notice).data)
