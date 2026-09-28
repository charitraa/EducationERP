from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.mixins import OrganizationScopedViewSet
from core.common.permissions import IsSameOrganization
from core.permissions.selectors import campus_ids_with_permission

from . import services
from .models import SupportTicket
from .serializers import (
    AddCommentSerializer,
    AssignTicketSerializer,
    SupportTicketSerializer,
    TicketCommentSerializer,
)
from .services import MANAGE

TAG = "support"


@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="List support tickets"),
    retrieve=extend_schema(tags=[TAG], summary="Retrieve a support ticket"),
    create=extend_schema(tags=[TAG], summary="Raise a support ticket"),
)
class SupportTicketViewSet(OrganizationScopedViewSet):
    # No PUT/PATCH/DELETE: the workflow only ever moves through the actions below —
    # a bare PATCH would let a raiser edit status/assignment directly, bypassing them
    # (the same superuser-bypasses-HasPermission gap fixed in Phases 5-7's admit-card/
    # invoice/event-registration viewsets).
    http_method_names = ["get", "post", "head", "options"]
    queryset = SupportTicket.objects.select_related("campus", "raised_by", "assigned_to")
    serializer_class = SupportTicketSerializer
    audit_module = "support"
    filterset_fields = ["status", "assigned_to", "campus"]
    required_permissions = {"assign": [MANAGE]}

    def get_permissions(self):
        if self.action == "assign":
            return super().get_permissions()
        return [IsAuthenticated(), IsSameOrganization()]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_platform_admin:
            return qs
        own = Q(raised_by=user) | Q(assigned_to=user)
        campus_ids = campus_ids_with_permission(user, MANAGE)
        if campus_ids is None:
            return qs
        if campus_ids:
            return qs.filter(Q(campus_id__in=campus_ids) | own)
        return qs.filter(own)

    def get_create_kwargs(self) -> dict:
        kwargs = super().get_create_kwargs()
        kwargs["raised_by"] = self.request.user
        kwargs["campus"] = services.resolve_campus_for(self.request.user)
        return kwargs

    @extend_schema(tags=[TAG], summary="Assign the ticket", request=AssignTicketSerializer,
                   responses={200: SupportTicketSerializer})
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        ticket = self.get_object()
        serializer = AssignTicketSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        ticket = services.assign_ticket(ticket, serializer.validated_data["assigned_to"], by=request.user)
        return Response(SupportTicketSerializer(ticket).data)

    @extend_schema(tags=[TAG], summary="Resolve the ticket", request=None, responses={200: SupportTicketSerializer})
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        ticket = self.get_object()
        services.ensure_can_manage(request.user, ticket)
        ticket = services.resolve_ticket(ticket, by=request.user)
        return Response(SupportTicketSerializer(ticket).data)

    @extend_schema(tags=[TAG], summary="Close the ticket", request=None, responses={200: SupportTicketSerializer})
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        ticket = self.get_object()
        services.ensure_can_close(request.user, ticket)
        ticket = services.close_ticket(ticket, by=request.user)
        return Response(SupportTicketSerializer(ticket).data)

    @extend_schema(tags=[TAG], summary="Comments on the ticket, or add one", request=AddCommentSerializer,
                   responses={200: TicketCommentSerializer(many=True), 201: TicketCommentSerializer})
    @action(detail=True, methods=["get", "post"])
    def comments(self, request, pk=None):
        ticket = self.get_object()
        if request.method == "GET":
            return Response(TicketCommentSerializer(ticket.comments.select_related("author"), many=True).data)
        serializer = AddCommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = ticket.comments.create(organization_id=ticket.organization_id, author=request.user,
                                         body=serializer.validated_data["body"])
        return Response(TicketCommentSerializer(comment).data, status=status.HTTP_201_CREATED)
