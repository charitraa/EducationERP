from datetime import timedelta

from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.exceptions import ConflictError
from core.common.mixins import CampusScopedMixin, CampusScopedViewSet, OrganizationScopedMixin
from core.common.permissions import HasPermission, IsSameOrganization
from modules.staff.selectors import staff_member_for_user

from . import selectors, services
from .models import (
    Assignment,
    Driver,
    FuelLog,
    Maintenance,
    Route,
    Stop,
    Trip,
    TripRecord,
    Vehicle,
    VehicleDocument,
)
from .serializers import (
    AssignmentSerializer,
    AssignRiderSerializer,
    DriverSerializer,
    EndAssignmentSerializer,
    FuelLogSerializer,
    TransportInvoicesSerializer,
    VehicleMaintenanceSerializer,
    BoardingMarksSerializer,
    OpenTripSerializer,
    RouteSerializer,
    StopSerializer,
    TripListSerializer,
    TripRecordSerializer,
    TripSerializer,
    VehicleDocumentSerializer,
    VehicleSerializer,
)
from .services import MANAGE, VIEW

TAG = "transport"
FINANCE_MANAGE = "finance.manage"
READ = {"list": [VIEW], "retrieve": [VIEW]}
CRUD = {**READ, "create": [MANAGE], "update": [MANAGE], "partial_update": [MANAGE], "destroy": [MANAGE]}
TRUE = ("1", "true", "True")
CRUD_DOCS = {a: extend_schema(tags=[TAG]) for a in
             ("list", "retrieve", "create", "update", "partial_update", "destroy")}


def _date(request, name, default=None):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = parse_date(raw)
    except ValueError:
        value = None
    if value is None:
        raise ValidationError({name: "Give a date as YYYY-MM-DD."})
    return value


def _days(request, name):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None
    try:
        days = int(raw)
    except ValueError:
        raise ValidationError({name: "Give a whole number of days."})
    if not 0 <= days <= 3650:
        raise ValidationError({name: "Between 0 and 3650 days."})
    return timezone.localdate() + timedelta(days=days)


class VehicleRecordViewSet(CampusScopedViewSet):
    """Records about one vehicle, reaching a campus through it."""

    campus_field = "vehicle__campus"
    audit_module = "transport"

    def campus_of(self, validated_data):
        vehicle = validated_data.get("vehicle")
        return vehicle.campus if vehicle is not None else None


# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------
@extend_schema_view(**CRUD_DOCS)
class VehicleViewSet(CampusScopedViewSet):
    queryset = Vehicle.objects.select_related("campus")
    serializer_class = VehicleSerializer
    audit_module = "transport"
    filterset_fields = ["campus", "kind", "is_active"]
    search_fields = ["name", "registration_number"]
    required_permissions = CRUD

    def perform_destroy(self, instance):
        # A soft delete skips the database's PROTECT, so the check lives here.
        if (instance.routes.exists() or instance.trips.exists() or instance.maintenance.exists()
                or instance.fuel_logs.exists()):
            raise ConflictError("This vehicle has routes, trips or service history; deactivate it instead.",
                                code="in_use")
        super().perform_destroy(instance)


@extend_schema_view(
    list=extend_schema(tags=[TAG], parameters=[
        OpenApiParameter("expiring_within", int, description="Only papers expiring within this many days "
                                                             "(including those already expired).")]),
    **{a: extend_schema(tags=[TAG]) for a in ("retrieve", "create", "update", "partial_update", "destroy")},
)
class VehicleDocumentViewSet(VehicleRecordViewSet):
    queryset = VehicleDocument.objects.select_related("vehicle")
    serializer_class = VehicleDocumentSerializer
    filterset_fields = ["vehicle", "kind"]
    search_fields = ["number"]
    required_permissions = CRUD

    def get_queryset(self):
        qs = super().get_queryset()
        until = _days(self.request, "expiring_within")
        if until is not None:
            qs = qs.filter(expires_on__lte=until)
        return qs


@extend_schema_view(
    list=extend_schema(tags=[TAG], parameters=[
        OpenApiParameter("license_expiring_within", int, description="Only licences expiring within this many "
                                                                     "days (including expired).")]),
    **{a: extend_schema(tags=[TAG]) for a in ("retrieve", "create", "update", "partial_update", "destroy")},
)
class DriverViewSet(CampusScopedViewSet):
    campus_field = "staff__campus"
    queryset = Driver.objects.select_related("staff")
    serializer_class = DriverSerializer
    audit_module = "transport"
    filterset_fields = ["role", "is_active", "staff"]
    search_fields = ["staff__first_name", "staff__last_name", "license_number"]
    required_permissions = CRUD

    def get_queryset(self):
        qs = super().get_queryset()
        until = _days(self.request, "license_expiring_within")
        if until is not None:
            qs = qs.filter(license_expires_on__lte=until)
        return qs

    def campus_of(self, validated_data):
        staff = validated_data.get("staff")
        return staff.campus if staff is not None else None

    def perform_destroy(self, instance):
        if (instance.routes_driven.exists() or instance.routes_assisted.exists()
                or Trip.objects.filter(Q(driver=instance) | Q(assistant=instance)).exists()):
            raise ConflictError("This person crews routes or has driven trips; deactivate them instead.",
                                code="in_use")
        super().perform_destroy(instance)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@extend_schema_view(**CRUD_DOCS)
class RouteViewSet(CampusScopedViewSet):
    queryset = Route.objects.select_related("campus", "vehicle", "driver__staff", "assistant__staff",
                                            "fee_category").prefetch_related("stops__route")
    serializer_class = RouteSerializer
    audit_module = "transport"
    filterset_fields = ["campus", "vehicle", "driver", "is_active"]
    search_fields = ["code", "name", "stops__name"]
    required_permissions = CRUD

    def get_queryset(self):
        today = timezone.localdate()
        riding = (Q(assignments__start_date__lte=today, assignments__deleted_at__isnull=True)
                  & (Q(assignments__end_date__isnull=True) | Q(assignments__end_date__gte=today)))
        return (super().get_queryset().annotate(rider_count=Count("assignments", filter=riding, distinct=True))
                .order_by("campus_id", "code"))

    def perform_destroy(self, instance):
        if instance.stops.exists() or instance.assignments.exists() or instance.trips.exists():
            raise ConflictError("This route has stops, riders or trips; deactivate it instead.", code="in_use")
        super().perform_destroy(instance)


@extend_schema_view(**CRUD_DOCS)
class StopViewSet(CampusScopedViewSet):
    campus_field = "route__campus"
    queryset = Stop.objects.select_related("route")
    serializer_class = StopSerializer
    audit_module = "transport"
    filterset_fields = ["route"]
    search_fields = ["name", "landmark"]
    required_permissions = CRUD

    def campus_of(self, validated_data):
        route = validated_data.get("route")
        return route.campus if route is not None else None

    def perform_destroy(self, instance):
        if instance.assignments.exists():
            raise ConflictError("Riders board at this stop.", code="in_use")
        super().perform_destroy(instance)


# ---------------------------------------------------------------------------
# Riders
# ---------------------------------------------------------------------------
@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="List riders", parameters=[
        OpenApiParameter("current", bool, description="Only riders on their route today.")]),
    retrieve=extend_schema(tags=[TAG], summary="Retrieve a rider assignment"),
    create=extend_schema(tags=[TAG], summary="Put a student or staff member on a route",
                         request=AssignRiderSerializer, responses={201: AssignmentSerializer}),
)
class AssignmentViewSet(CampusScopedViewSet):
    # History: no edit and no delete. A rider changing route is ended here and assigned there.
    http_method_names = ["get", "post", "head", "options"]
    campus_field = "route__campus"
    queryset = Assignment.objects.select_related("route", "stop", "student", "staff")
    serializer_class = AssignmentSerializer
    audit_module = "transport"
    service_audits_create = True
    filterset_fields = ["route", "stop", "student", "staff", "direction"]
    search_fields = ["student__first_name", "student__last_name", "student__student_number",
                     "staff__first_name", "staff__last_name"]
    required_permissions = {**READ, "create": [MANAGE], "end": [MANAGE],
                            "generate_invoices": [MANAGE, FINANCE_MANAGE]}

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("current") in TRUE:
            qs = qs.filter(pk__in=services.active_on(timezone.localdate()).values("pk"))
        return qs

    def create(self, request, *args, **kwargs):
        serializer = AssignRiderSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        assignment = services.assign_rider(by=request.user, **serializer.validated_data)
        return Response(AssignmentSerializer(assignment).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="My route (or my children's), current and past",
                   responses={200: AssignmentSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response(AssignmentSerializer(selectors.assignments_for_user(request.user), many=True).data)

    @extend_schema(tags=[TAG], summary="Stop riding after a day", request=EndAssignmentSerializer,
                   responses={200: AssignmentSerializer})
    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        assignment = self.get_object()
        serializer = EndAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignment = services.end_assignment(assignment, by=request.user, **serializer.validated_data)
        return Response(AssignmentSerializer(assignment).data)

    @extend_schema(tags=[TAG], summary="Bill the term's transport fees (one invoice per student, safe to rerun)",
                   request=TransportInvoicesSerializer, responses={200: dict})
    @action(detail=False, methods=["post"], url_path="generate-invoices")
    def generate_invoices(self, request):
        serializer = TransportInvoicesSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        return Response(services.generate_term_invoices(by=request.user, **serializer.validated_data))


# ---------------------------------------------------------------------------
# Trips
# ---------------------------------------------------------------------------
CREW_ACTIONS = ("create", "mark", "complete", "mine")


@extend_schema_view(
    list=extend_schema(tags=[TAG], summary="List trips", responses={200: TripListSerializer(many=True)},
                       parameters=[OpenApiParameter("date_from", str), OpenApiParameter("date_to", str)]),
    retrieve=extend_schema(tags=[TAG], summary="A trip with its roster"),
    create=extend_schema(tags=[TAG], summary="Open a trip (crew or office); returns the existing one if open",
                         request=OpenTripSerializer, responses={200: TripSerializer, 201: TripSerializer}),
)
class TripViewSet(CampusScopedViewSet):
    """The crew opens, marks and completes their own route's trips with no
    permission; ``ensure_can_run`` checks they crew it (or hold
    ``transport.manage`` for its campus). Reading trips needs ``transport.view``."""

    http_method_names = ["get", "post", "head", "options"]
    campus_field = "route__campus"
    queryset = Trip.objects.select_related("route", "vehicle", "driver__staff", "assistant__staff").prefetch_related(
        Prefetch("records", queryset=TripRecord.objects.all()))
    serializer_class = TripSerializer
    audit_module = "transport"
    service_audits_create = True
    filterset_fields = ["route", "date", "direction", "status", "vehicle", "driver"]
    required_permissions = READ

    def get_permissions(self):
        if self.action in CREW_ACTIONS:
            return [IsAuthenticated(), IsSameOrganization()]
        return super().get_permissions()

    def get_serializer_class(self):
        return TripListSerializer if self.action == "list" else TripSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        for name, lookup in (("date_from", "date__gte"), ("date_to", "date__lte")):
            value = _date(self.request, name)
            if value is not None:
                qs = qs.filter(**{lookup: value})
        return qs

    def create(self, request, *args, **kwargs):
        serializer = OpenTripSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        trip, created = services.open_trip(route=data["route"], date=data.get("date") or timezone.localdate(),
                                           direction=data["direction"], by=request.user)
        trip = self.get_queryset().get(pk=trip.pk)
        return Response(TripSerializer(trip).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @extend_schema(tags=[TAG], summary="Routes I crew, with the day's trips (open them with POST)",
                   parameters=[OpenApiParameter("date", str)], responses={200: dict})
    @action(detail=False, methods=["get"])
    def mine(self, request):
        day = _date(request, "date", timezone.localdate())
        staff = staff_member_for_user(request.user)
        if staff is None:
            return Response({"date": day, "routes": []})
        routes = (Route.objects.filter(organization_id=request.user.organization_id, is_active=True)
                  .filter(Q(driver__staff=staff) | Q(assistant__staff=staff)).select_related("vehicle"))
        trips = {(t.route_id, t.direction): t.pk for t in Trip.objects.filter(route__in=routes, date=day)}
        return Response({"date": day, "routes": [
            {"route": r.pk, "name": r.name, "vehicle": r.vehicle.name if r.vehicle else None,
             "pickup_trip": trips.get((r.pk, "pickup")), "drop_trip": trips.get((r.pk, "drop"))}
            for r in routes]})

    @extend_schema(tags=[TAG], summary="Mark who boarded (crew or office)", request=BoardingMarksSerializer,
                   responses={200: TripSerializer})
    @action(detail=True, methods=["post"])
    def mark(self, request, pk=None):
        trip = self.get_object()
        serializer = BoardingMarksSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        services.mark_trip(trip, serializer.validated_data["entries"], by=request.user)
        return Response(TripSerializer(self.get_queryset().get(pk=trip.pk)).data)

    @extend_schema(tags=[TAG], summary="Complete the trip (crew or office)", request=None,
                   responses={200: TripSerializer})
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        trip = services.complete_trip(self.get_object(), by=request.user)
        return Response(TripSerializer(self.get_queryset().get(pk=trip.pk)).data)


class ReadOnlyCampusViewSet(CampusScopedMixin, OrganizationScopedMixin, mixins.ListModelMixin,
                            mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """List/retrieve only — no create route exists at all."""

    permission_classes = [HasPermission, IsSameOrganization]
    required_permissions = READ


@extend_schema_view(list=extend_schema(tags=[TAG], summary="List boarding marks"),
                    retrieve=extend_schema(tags=[TAG], summary="Retrieve a boarding mark"))
class TripRecordViewSet(ReadOnlyCampusViewSet):
    campus_field = "trip__route__campus"
    queryset = TripRecord.objects.select_related("trip__route", "assignment__stop", "assignment__student",
                                                 "assignment__staff")
    serializer_class = TripRecordSerializer
    filterset_fields = ["trip", "assignment", "status", "assignment__student", "assignment__staff",
                        "trip__route", "trip__date"]

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    @extend_schema(tags=[TAG], summary="My (or my children's) boarding history",
                   responses={200: TripRecordSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def me(self, request):
        rows = selectors.trip_records_for_user(request.user)
        page = self.paginate_queryset(rows)
        return self.get_paginated_response(TripRecordSerializer(page, many=True).data)


# ---------------------------------------------------------------------------
# Upkeep
# ---------------------------------------------------------------------------
@extend_schema_view(**CRUD_DOCS)
class VehicleMaintenanceViewSet(VehicleRecordViewSet):
    queryset = Maintenance.objects.select_related("vehicle")
    serializer_class = VehicleMaintenanceSerializer
    filterset_fields = ["vehicle", "kind"]
    search_fields = ["description", "vendor"]
    required_permissions = CRUD

    def get_create_kwargs(self):
        return {**super().get_create_kwargs(), "recorded_by": self.request.user}


@extend_schema_view(**CRUD_DOCS)
class FuelLogViewSet(VehicleRecordViewSet):
    queryset = FuelLog.objects.select_related("vehicle")
    serializer_class = FuelLogSerializer
    filterset_fields = ["vehicle"]
    required_permissions = CRUD

    def get_create_kwargs(self):
        return {**super().get_create_kwargs(), "recorded_by": self.request.user}
