from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import services
from .models import Notification
from .serializers import NotificationSerializer

TAG = "notifications"


@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="My notifications"),
    retrieve=extend_schema(tags=[TAG], summary="One of my notifications"),
)
class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """Every notification is self-scoped: a user only ever sees their own,
    across every organization, so there is no other tenant's row reachable
    here to leak — the queryset itself is the tenant boundary."""

    http_method_names = ["get", "post", "head", "options"]
    queryset = Notification.objects.all()  # the router/tenant-sweep introspect this; get_queryset narrows it
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["is_read", "event_type"]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)

    @extend_schema(tags=[TAG], summary="Mark one notification read", request=None,
                   responses={200: NotificationSerializer})
    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        services.mark_read(notification)
        return Response(NotificationSerializer(notification).data)

    @extend_schema(tags=[TAG], summary="Mark every notification read", request=None, responses={200: None})
    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        count = services.mark_all_read(request.user)
        return Response({"marked": count})

    @extend_schema(tags=[TAG], summary="How many are unread", responses={200: None})
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"unread": self.get_queryset().filter(is_read=False).count()})
