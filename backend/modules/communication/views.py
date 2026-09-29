from django.db.models import Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.mixins import CampusScopedViewSet, OrganizationScopedViewSet
from core.common.permissions import IsSameOrganization
from core.permissions.selectors import campus_ids_with_permission
from modules.staff.selectors import staff_member_for_user

from . import services
from .models import Appointment, AppointmentSlot, MessageThread
from .serializers import (
    AppointmentSerializer,
    AppointmentSlotSerializer,
    BookAppointmentSerializer,
    CancelAppointmentSerializer,
    MessageSerializer,
    MessageThreadSerializer,
    SendMessageSerializer,
    StartThreadSerializer,
)
from .services import MANAGE, PUBLISH

TAG = "communication"


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------
@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="My conversations"),
    retrieve=extend_schema(tags=[TAG], summary="One conversation"),
)
class MessageThreadViewSet(OrganizationScopedViewSet):
    http_method_names = ["get", "post", "head", "options"]
    queryset = MessageThread.objects.select_related("staff_user", "other_user", "campus")
    serializer_class = MessageThreadSerializer
    filterset_fields = ["is_closed"]

    def get_permissions(self):
        return [IsAuthenticated(), IsSameOrganization()]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_platform_admin:
            return qs
        return qs.filter(Q(staff_user=user) | Q(other_user=user))

    @extend_schema(tags=[TAG], summary="Start a conversation (staff)", request=StartThreadSerializer,
                   responses={201: MessageThreadSerializer})
    def create(self, request, *args, **kwargs):
        serializer = StartThreadSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        thread = services.start_thread(staff_user=request.user, other_user=data["other_user"],
                                       campus=data["campus"], subject=data.get("subject", ""))
        return Response(MessageThreadSerializer(thread).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Read or send a message", request=SendMessageSerializer,
                   responses={200: MessageSerializer(many=True), 201: MessageSerializer})
    @action(detail=True, methods=["get", "post"])
    def messages(self, request, pk=None):
        thread = self.get_object()
        if request.method == "GET":
            return Response(MessageSerializer(thread.messages.select_related("sender"), many=True).data)
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = services.send_message(thread, request.user, serializer.validated_data["body"])
        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Close the conversation", request=None, responses={200: MessageThreadSerializer})
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        thread = self.get_object()
        services.ensure_participant(request.user, thread)
        thread = services.close_thread(thread)
        return Response(MessageThreadSerializer(thread).data)


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------
@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="List appointment slots"),
    retrieve=extend_schema(tags=[TAG], summary="Retrieve an appointment slot"),
    create=extend_schema(tags=[TAG], summary="Publish an appointment slot"),
    update=extend_schema(tags=[TAG], summary="Replace an appointment slot"),
    partial_update=extend_schema(tags=[TAG], summary="Update an appointment slot"),
    destroy=extend_schema(tags=[TAG], summary="Delete an appointment slot"),
)
class AppointmentSlotViewSet(CampusScopedViewSet):
    queryset = AppointmentSlot.objects.select_related("staff", "campus")
    serializer_class = AppointmentSlotSerializer
    audit_module = "communication"
    filterset_fields = ["campus", "staff", "is_cancelled"]
    required_permissions = {"create": [PUBLISH], "update": [PUBLISH], "partial_update": [PUBLISH],
                            "destroy": [PUBLISH], "cancel": [PUBLISH]}

    def get_permissions(self):
        # Reading the office's published slots is open to anyone in the organization,
        # so a parent or student can see what's available to book.
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), IsSameOrganization()]
        return super().get_permissions()

    def perform_create(self, serializer):
        data = serializer.validated_data
        services.ensure_can_manage_slot(self.request.user, staff_id=data["staff"].pk, campus_id=data["campus"].pk)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        services.ensure_can_manage_slot(self.request.user, staff_id=serializer.instance.staff_id,
                                        campus_id=serializer.instance.campus_id)
        data = serializer.validated_data
        services.ensure_can_manage_slot(
            self.request.user, staff_id=data["staff"].pk if "staff" in data else serializer.instance.staff_id,
            campus_id=data["campus"].pk if "campus" in data else serializer.instance.campus_id)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        services.ensure_can_manage_slot(self.request.user, staff_id=instance.staff_id, campus_id=instance.campus_id)
        if instance.appointments.exclude(status="cancelled").exists():
            from core.common.exceptions import ConflictError

            raise ConflictError("This slot has a live booking; cancel it first.", code="in_use")
        super().perform_destroy(instance)

    @extend_schema(tags=[TAG], summary="Cancel the slot", request=None, responses={200: AppointmentSlotSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        slot = self.get_object()
        services.ensure_can_manage_slot(request.user, staff_id=slot.staff_id, campus_id=slot.campus_id)
        slot.is_cancelled = True
        slot.save(update_fields=["is_cancelled", "updated_at"])
        slot.appointments.exclude(status="cancelled").update(status="cancelled", cancelled_reason="Slot cancelled")
        return Response(AppointmentSlotSerializer(slot).data)


@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="List appointments"),
    retrieve=extend_schema(tags=[TAG], summary="Retrieve an appointment"),
    create=extend_schema(tags=[TAG], summary="Book an open slot"),
)
class AppointmentViewSet(OrganizationScopedViewSet):
    http_method_names = ["get", "post", "head", "options"]
    queryset = Appointment.objects.select_related("slot__staff", "slot__campus", "requested_by", "student")
    serializer_class = AppointmentSerializer
    filterset_fields = ["status", "slot"]

    def get_permissions(self):
        return [IsAuthenticated(), IsSameOrganization()]

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if user.is_platform_admin:
            return qs
        own = Q(requested_by=user)
        staff = staff_member_for_user(user)
        if staff is not None:
            own |= Q(slot__staff=staff)
        campus_ids = campus_ids_with_permission(user, MANAGE)
        if campus_ids is None:
            return qs
        if campus_ids:
            return qs.filter(Q(slot__campus_id__in=campus_ids) | own)
        return qs.filter(own)

    def create(self, request, *args, **kwargs):
        serializer = BookAppointmentSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        services.ensure_can_book_for(request.user, data.get("student"))
        appointment = services.book_slot(data["slot"], requested_by=request.user, student=data.get("student"),
                                         reason=data.get("reason", ""))
        return Response(AppointmentSerializer(appointment).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Approve the appointment", request=None, responses={200: AppointmentSerializer})
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        appointment = self.get_object()
        appointment = services.approve_appointment(appointment, by=request.user)
        return Response(AppointmentSerializer(appointment).data)

    @extend_schema(tags=[TAG], summary="Cancel the appointment", request=CancelAppointmentSerializer,
                   responses={200: AppointmentSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        appointment = self.get_object()
        serializer = CancelAppointmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.ensure_can_cancel(request.user, appointment)
        appointment = services.cancel_appointment(appointment, serializer.validated_data["reason"])
        return Response(AppointmentSerializer(appointment).data)

    @extend_schema(tags=[TAG], summary="Mark the appointment completed", request=None,
                   responses={200: AppointmentSerializer})
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        appointment = self.get_object()
        appointment = services.complete_appointment(appointment, by=request.user)
        return Response(AppointmentSerializer(appointment).data)
