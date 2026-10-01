from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.exceptions import ConflictError
from core.common.mixins import CampusScopedViewSet, OrganizationScopedViewSet
from modules.staff.selectors import staff_member_for_user
from modules.students.selectors import student_for_user

from . import selectors, services
from .models import HOLDING, Allocation, Bed, Building, Complaint, Floor, HostelRoom, RoomType
from .serializers import (
    AllocateSerializer,
    AllocationSerializer,
    AssignComplaintSerializer,
    BedSerializer,
    BuildingSerializer,
    CheckOutSerializer,
    ComplaintSerializer,
    FloorSerializer,
    HostelInvoicesSerializer,
    MoveSerializer,
    MyComplaintSerializer,
    RaiseComplaintSerializer,
    HostelReasonSerializer,
    ResolveComplaintSerializer,
    HostelRoomSerializer,
    RoomTypeSerializer,
)
from .services import MANAGE, VIEW

TAG = "hostel"
FINANCE_MANAGE = "finance.manage"
READ = {"list": [VIEW], "retrieve": [VIEW]}
CRUD = {**READ, "create": [MANAGE], "update": [MANAGE], "partial_update": [MANAGE], "destroy": [MANAGE]}
TRUE = ("1", "true", "True")


def _schema(noun: str, *actions):
    summaries = {"list": f"List {noun}s", "retrieve": f"Retrieve a {noun}",
                 "create": f"Create a {noun}", "update": f"Replace a {noun}",
                 "partial_update": f"Update a {noun}", "destroy": f"Delete a {noun}"}
    return extend_schema_view(**{a: extend_schema(tags=[TAG], summary=summaries[a])
                                 for a in actions or summaries})


def _taken():
    """The bed (``OuterRef``) is reserved or someone is checked in."""
    return Exists(Allocation.objects.filter(bed=OuterRef("pk"), status__in=HOLDING))


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------
@_schema("building")
class BuildingViewSet(CampusScopedViewSet):
    queryset = Building.objects.select_related("campus", "warden")
    serializer_class = BuildingSerializer
    audit_module = "hostel"
    filterset_fields = ["campus", "gender", "is_active"]
    search_fields = ["code", "name"]
    required_permissions = CRUD

    def perform_destroy(self, instance):
        # A soft delete skips the database's PROTECT, so the check lives here.
        if instance.floors.exists() or instance.rooms.exists() or instance.complaints.exists():
            raise ConflictError("This building has floors, rooms or complaints; deactivate it instead.",
                                code="in_use")
        super().perform_destroy(instance)


@_schema("floor")
class FloorViewSet(CampusScopedViewSet):
    campus_field = "building__campus"
    queryset = Floor.objects.select_related("building")
    serializer_class = FloorSerializer
    audit_module = "hostel"
    filterset_fields = ["building"]
    required_permissions = CRUD

    def campus_of(self, validated_data):
        building = validated_data.get("building")
        return building.campus if building is not None else None

    def perform_destroy(self, instance):
        if instance.rooms.exists():
            raise ConflictError("Rooms are on this floor.", code="in_use")
        super().perform_destroy(instance)


@_schema("room type")
class RoomTypeViewSet(OrganizationScopedViewSet):
    queryset = RoomType.objects.select_related("fee_category")
    serializer_class = RoomTypeSerializer
    audit_module = "hostel"
    filterset_fields = ["is_active"]
    search_fields = ["code", "name"]
    required_permissions = CRUD

    def perform_destroy(self, instance):
        if instance.rooms.exists():
            raise ConflictError("Rooms are of this type; deactivate it instead.", code="in_use")
        super().perform_destroy(instance)


@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="List rooms", parameters=[
        OpenApiParameter("available", bool, description="Only rooms with a free bed in service.")]),
    retrieve=extend_schema(tags=[TAG]), create=extend_schema(tags=[TAG]), update=extend_schema(tags=[TAG]),
    partial_update=extend_schema(tags=[TAG]), destroy=extend_schema(tags=[TAG]),
)
class HostelRoomViewSet(CampusScopedViewSet):
    campus_field = "building__campus"
    queryset = HostelRoom.objects.select_related("building", "floor", "room_type").prefetch_related("beds")
    serializer_class = HostelRoomSerializer
    audit_module = "hostel"
    filterset_fields = ["building", "floor", "room_type", "is_active"]
    search_fields = ["number", "building__name"]
    required_permissions = CRUD

    def get_queryset(self):
        qs = super().get_queryset().annotate(
            occupied_beds=Count("beds__allocations", filter=Q(beds__allocations__status__in=HOLDING,
                                                             beds__allocations__deleted_at__isnull=True))
        ).order_by("building_id", "floor_id", "number")
        if self.request.query_params.get("available") in TRUE:
            free = Bed.objects.filter(room=OuterRef("pk"), is_active=True).exclude(_taken())
            qs = qs.filter(is_active=True, building__is_active=True).filter(Exists(free))
        return qs

    def campus_of(self, validated_data):
        building = validated_data.get("building")
        return building.campus if building is not None else None

    def perform_destroy(self, instance):
        if instance.beds.exists() or instance.complaints.exists():
            raise ConflictError("This room has beds or complaints; deactivate it instead.", code="in_use")
        super().perform_destroy(instance)


@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="List beds", parameters=[
        OpenApiParameter("available", bool, description="Only free beds in service.")]),
    retrieve=extend_schema(tags=[TAG]), create=extend_schema(tags=[TAG]), update=extend_schema(tags=[TAG]),
    partial_update=extend_schema(tags=[TAG]), destroy=extend_schema(tags=[TAG]),
)
class BedViewSet(CampusScopedViewSet):
    campus_field = "room__building__campus"
    queryset = Bed.objects.select_related("room__building").prefetch_related(
        Prefetch("allocations", queryset=Allocation.objects.filter(status__in=HOLDING)
                 .select_related("student", "staff")))
    serializer_class = BedSerializer
    audit_module = "hostel"
    filterset_fields = ["room", "room__building", "is_active"]
    required_permissions = CRUD

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("available") in TRUE:
            qs = qs.filter(is_active=True, room__is_active=True, room__building__is_active=True).exclude(_taken())
        return qs

    def campus_of(self, validated_data):
        room = validated_data.get("room")
        return room.building.campus if room is not None else None

    def perform_destroy(self, instance):
        if Allocation.all_objects.filter(bed=instance).exists():
            raise ConflictError("This bed has been allocated before; take it out of service instead.",
                                code="in_use")
        super().perform_destroy(instance)


# ---------------------------------------------------------------------------
# Allocations
# ---------------------------------------------------------------------------
@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="List allocations", parameters=[
        OpenApiParameter("current", bool, description="Only reserved or checked-in stays.")]),
    retrieve=extend_schema(tags=[TAG], summary="Retrieve an allocation"),
    create=extend_schema(tags=[TAG], summary="Reserve a bed for a student or staff member",
                         request=AllocateSerializer, responses={201: AllocationSerializer}),
)
class AllocationViewSet(CampusScopedViewSet):
    # Stays are history: no edit and no delete. They change only through the actions.
    http_method_names = ["get", "post", "head", "options"]
    campus_field = "bed__room__building__campus"
    queryset = Allocation.objects.select_related("bed__room__building", "student", "staff")
    serializer_class = AllocationSerializer
    audit_module = "hostel"
    service_audits_create = True
    filterset_fields = ["bed", "bed__room", "bed__room__building", "student", "staff", "status"]
    search_fields = ["student__first_name", "student__last_name", "student__student_number",
                     "staff__first_name", "staff__last_name", "staff__employee_number"]
    required_permissions = {**READ, "create": [MANAGE], "check_in": [MANAGE], "check_out": [MANAGE],
                            "cancel": [MANAGE], "move": [MANAGE],
                            "generate_invoices": [MANAGE, FINANCE_MANAGE]}

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("current") in TRUE:
            qs = qs.filter(status__in=HOLDING)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = AllocateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        allocation = services.allocate_hostel_room(by=request.user, **serializer.validated_data)
        return Response(AllocationSerializer(allocation).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="My bed (or my children's), current and past",
                   responses={200: AllocationSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response(AllocationSerializer(selectors.allocations_for_user(request.user), many=True).data)

    @extend_schema(tags=[TAG], summary="Check in (on or after the reserved date)", request=None,
                   responses={200: AllocationSerializer})
    @action(detail=True, methods=["post"], url_path="check-in")
    def check_in(self, request, pk=None):
        return Response(AllocationSerializer(services.check_in(self.get_object(), by=request.user)).data)

    @extend_schema(tags=[TAG], summary="Check out", request=CheckOutSerializer,
                   responses={200: AllocationSerializer})
    @action(detail=True, methods=["post"], url_path="check-out")
    def check_out(self, request, pk=None):
        allocation = self.get_object()
        serializer = CheckOutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        allocation = services.check_out(allocation, by=request.user, **serializer.validated_data)
        return Response(AllocationSerializer(allocation).data)

    @extend_schema(tags=[TAG], summary="Cancel a reservation not yet checked into", request=HostelReasonSerializer,
                   responses={200: AllocationSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        allocation = self.get_object()
        serializer = HostelReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        allocation = services.cancel_allocation(allocation, serializer.validated_data["reason"], by=request.user)
        return Response(AllocationSerializer(allocation).data)

    @extend_schema(tags=[TAG], summary="Move to another bed (ends this stay, starts a new one)",
                   request=MoveSerializer, responses={201: AllocationSerializer})
    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        allocation = self.get_object()
        serializer = MoveSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        new = services.move(allocation, by=request.user, **serializer.validated_data)
        return Response(AllocationSerializer(new).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Bill the term's hostel fees (one invoice per student, safe to rerun)",
                   request=HostelInvoicesSerializer, responses={200: dict})
    @action(detail=False, methods=["post"], url_path="generate-invoices")
    def generate_invoices(self, request):
        serializer = HostelInvoicesSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        return Response(services.generate_term_invoices(by=request.user, **serializer.validated_data))


# ---------------------------------------------------------------------------
# Complaints
# ---------------------------------------------------------------------------
@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="List complaints"),
    retrieve=extend_schema(tags=[TAG], summary="Retrieve a complaint"),
    create=extend_schema(tags=[TAG], summary="Record a complaint (office)", request=RaiseComplaintSerializer,
                         responses={201: ComplaintSerializer}),
)
class ComplaintViewSet(CampusScopedViewSet):
    http_method_names = ["get", "post", "head", "options"]
    campus_field = "building__campus"
    queryset = Complaint.objects.select_related("building", "room", "assigned_to")
    serializer_class = ComplaintSerializer
    audit_module = "hostel"
    service_audits_create = True
    filterset_fields = ["building", "room", "status", "category", "assigned_to"]
    search_fields = ["title", "description"]
    required_permissions = {**READ, "create": [MANAGE], "assign": [MANAGE], "resolve": [MANAGE],
                            "reject": [MANAGE]}

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        serializer = RaiseComplaintSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        complaint = services.raise_complaint(by=request.user, **serializer.validated_data)
        return Response(ComplaintSerializer(complaint).data, status=status.HTTP_201_CREATED)

    @extend_schema(methods=["get"], tags=[TAG], summary="Complaints I raised",
                   responses={200: ComplaintSerializer(many=True)})
    @extend_schema(methods=["post"], tags=[TAG], summary="Raise a complaint about my room",
                   request=MyComplaintSerializer, responses={201: ComplaintSerializer})
    @action(detail=False, methods=["get", "post"])
    def me(self, request):
        if request.method == "GET":
            mine = Complaint.objects.filter(raised_by=request.user).select_related("building", "room",
                                                                                   "assigned_to")
            return Response(ComplaintSerializer(mine, many=True).data)
        student, staff = student_for_user(request.user), staff_member_for_user(request.user)
        stay = (services.current_allocation(student=student) if student is not None else None) or (
            services.current_allocation(staff=staff) if staff is not None else None)
        if stay is None or stay.status != "checked_in":
            raise NotFound("You aren't checked into a hostel bed.")
        serializer = MyComplaintSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        complaint = services.raise_complaint(building=stay.bed.room.building, room=stay.bed.room, by=request.user,
                                             **serializer.validated_data)
        return Response(ComplaintSerializer(complaint).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Hand the complaint to a staff member", request=AssignComplaintSerializer,
                   responses={200: ComplaintSerializer})
    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        complaint = self.get_object()
        serializer = AssignComplaintSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        complaint = services.assign_complaint(complaint, by=request.user, **serializer.validated_data)
        return Response(ComplaintSerializer(complaint).data)

    @extend_schema(tags=[TAG], summary="Mark resolved", request=ResolveComplaintSerializer,
                   responses={200: ComplaintSerializer})
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        complaint = self.get_object()
        serializer = ResolveComplaintSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        complaint = services.resolve_complaint(complaint, by=request.user, **serializer.validated_data)
        return Response(ComplaintSerializer(complaint).data)

    @extend_schema(tags=[TAG], summary="Reject (not a valid complaint)", request=HostelReasonSerializer,
                   responses={200: ComplaintSerializer})
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        complaint = self.get_object()
        serializer = HostelReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        complaint = services.reject_complaint(complaint, by=request.user, **serializer.validated_data)
        return Response(ComplaintSerializer(complaint).data)
