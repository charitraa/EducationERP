from django.utils import timezone
from rest_framework import serializers

from core.common.exceptions import ServiceError
from core.common.serializers import ensure_unique_in_organization, ensure_unique_together, target_organization_id
from modules.academics.models import Term
from modules.staff.models import StaffMember
from modules.students.models import Student

from . import services
from .models import (
    Assignment,
    BoardingStatus,
    Direction,
    Driver,
    FuelLog,
    Maintenance,
    Route,
    Stop,
    Trip,
    TripDirection,
    TripRecord,
    Vehicle,
    VehicleDocument,
)


class OwnedSerializer(serializers.ModelSerializer):
    def own(self, value, label):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return value


class OwnedInput(serializers.Serializer):
    def own(self, value, label):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return value


class OwnedLine(serializers.Serializer):
    """A nested line; tenant checks go through the root serializer's view."""

    def own(self, value, label):
        if value is not None and value.organization_id != self.context["view"].get_target_organization_id():
            raise serializers.ValidationError(f"Unknown {label}.")
        return value


def _in_scope(serializer, campus):
    """Refuse a campus the caller's role doesn't cover, during validation, so
    the reply can't describe what's there."""
    view = serializer.context.get("view")
    if campus is not None and hasattr(view, "check_campus_allowed"):
        view.check_campus_allowed(campus)


# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------
class VehicleSerializer(OwnedSerializer):
    campus_name = serializers.CharField(source="campus.name", read_only=True)

    class Meta:
        model = Vehicle
        fields = ["id", "organization", "campus", "campus_name", "registration_number", "name", "kind", "capacity",
                  "make", "model", "year", "is_active", "note", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_campus(self, value):
        return self.own(value, "campus")

    def validate_registration_number(self, value):
        value = value.strip().upper()
        ensure_unique_in_organization(self, "registration_number", value)
        return value

    def validate_capacity(self, value):
        if value < 1:
            raise serializers.ValidationError("A vehicle needs at least one seat.")
        return value


class VehicleDocumentSerializer(OwnedSerializer):
    vehicle_name = serializers.CharField(source="vehicle.name", read_only=True)

    class Meta:
        model = VehicleDocument
        fields = ["id", "organization", "vehicle", "vehicle_name", "kind", "number", "issued_on", "expires_on",
                  "note", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_vehicle(self, value):
        value = self.own(value, "vehicle")
        _in_scope(self, value.campus)
        return value

    def validate(self, attrs):
        issued = attrs.get("issued_on", getattr(self.instance, "issued_on", None))
        expires = attrs.get("expires_on", getattr(self.instance, "expires_on", None))
        if issued and expires and expires < issued:
            raise serializers.ValidationError({"expires_on": "Can't expire before it was issued."})
        return attrs


class DriverSerializer(OwnedSerializer):
    name = serializers.CharField(source="staff.full_name", read_only=True)
    phone = serializers.CharField(source="staff.phone", read_only=True, default="")

    class Meta:
        model = Driver
        fields = ["id", "organization", "staff", "name", "phone", "role", "license_number", "license_category",
                  "license_expires_on", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_staff(self, value):
        value = self.own(value, "staff member")
        if self.instance is not None and value.pk != self.instance.staff_id:
            raise serializers.ValidationError("Can't be changed.")
        _in_scope(self, value.campus)
        clash = Driver.objects.filter(staff=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("This staff member is already on the crew list.")
        return value

    def validate(self, attrs):
        if (self.instance is not None and attrs.get("role") == "assistant"
                and self.instance.routes_driven.exists()):
            raise serializers.ValidationError({"role": "They drive a route; take them off it first."})
        return attrs


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
class StopSerializer(OwnedSerializer):
    fee = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Stop
        fields = ["id", "organization", "route", "sequence", "name", "landmark", "pickup_time", "drop_time",
                  "latitude", "longitude", "fee_per_term", "fee", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_route(self, value):
        value = self.own(value, "route")
        if self.instance is not None and value.pk != self.instance.route_id:
            raise serializers.ValidationError("A stop can't move to another route; add it there instead.")
        _in_scope(self, value.campus)
        return value

    def validate_fee_per_term(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("A fee can't be negative.")
        return value

    def validate(self, attrs):
        ensure_unique_together(self, attrs, ["route", "sequence"], "This route already has a stop at that place.")
        return attrs


class RouteSerializer(OwnedSerializer):
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    vehicle_name = serializers.CharField(source="vehicle.name", read_only=True, default=None)
    driver_name = serializers.CharField(source="driver.staff.full_name", read_only=True, default=None)
    assistant_name = serializers.CharField(source="assistant.staff.full_name", read_only=True, default=None)
    stops = StopSerializer(many=True, read_only=True)
    riders = serializers.SerializerMethodField()

    class Meta:
        model = Route
        fields = ["id", "organization", "campus", "campus_name", "code", "name", "vehicle", "vehicle_name",
                  "driver", "driver_name", "assistant", "assistant_name", "fee_per_term", "fee_category",
                  "is_active", "stops", "riders", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def get_riders(self, obj) -> int:
        cached = getattr(obj, "rider_count", None)
        return cached if cached is not None else services.active_on(timezone.localdate()).filter(route=obj).count()

    def validate_campus(self, value):
        value = self.own(value, "campus")
        if self.instance is not None and value.pk != self.instance.campus_id and (
                self.instance.assignments.exists() or self.instance.trips.exists()):
            raise serializers.ValidationError("This route has riders or trips, so its campus can't change.")
        return value

    def validate_vehicle(self, value):
        return self.own(value, "vehicle")

    def validate_driver(self, value):
        return self.own(value, "driver")

    def validate_assistant(self, value):
        return self.own(value, "assistant")

    def validate_fee_category(self, value):
        return self.own(value, "fee category")

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value

    def validate_fee_per_term(self, value):
        if value < 0:
            raise serializers.ValidationError("A fee can't be negative.")
        return value

    def validate(self, attrs):
        campus = attrs.get("campus", getattr(self.instance, "campus", None))
        vehicle = attrs.get("vehicle", getattr(self.instance, "vehicle", None))
        if vehicle is not None and campus is not None and vehicle.campus_id != campus.pk:
            raise serializers.ValidationError({"vehicle": "That vehicle belongs to another campus."})
        probe = Route(driver=attrs.get("driver", getattr(self.instance, "driver", None)),
                      assistant=attrs.get("assistant", getattr(self.instance, "assistant", None)))
        try:
            services.check_crew(probe)
        except ServiceError as exc:
            raise serializers.ValidationError({"driver": str(exc.detail)})
        return attrs


# ---------------------------------------------------------------------------
# Riders
# ---------------------------------------------------------------------------
class AssignmentSerializer(serializers.ModelSerializer):
    rider_name = serializers.CharField(read_only=True)
    route_name = serializers.CharField(source="route.name", read_only=True)
    stop_name = serializers.CharField(source="stop.name", read_only=True)
    pickup_time = serializers.TimeField(source="stop.pickup_time", read_only=True)
    drop_time = serializers.TimeField(source="stop.drop_time", read_only=True)

    class Meta:
        model = Assignment
        fields = ["id", "organization", "route", "route_name", "stop", "stop_name", "pickup_time", "drop_time",
                  "student", "staff", "rider_name", "direction", "start_date", "end_date", "end_reason",
                  "assigned_by", "created_at", "updated_at"]
        read_only_fields = fields


class AssignRiderSerializer(OwnedInput):
    route = serializers.PrimaryKeyRelatedField(queryset=Route.objects.all())
    stop = serializers.PrimaryKeyRelatedField(queryset=Stop.objects.all())
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all(), required=False, allow_null=True)
    staff = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.all(), required=False, allow_null=True)
    direction = serializers.ChoiceField(choices=Direction.choices, default=Direction.BOTH)
    start_date = serializers.DateField(required=False)

    def validate_route(self, value):
        value = self.own(value, "route")
        _in_scope(self, value.campus)
        return value

    def validate_stop(self, value):
        return self.own(value, "stop")

    def validate_student(self, value):
        return self.own(value, "student")

    def validate_staff(self, value):
        return self.own(value, "staff member")

    def validate(self, attrs):
        if (attrs.get("student") is None) == (attrs.get("staff") is None):
            raise serializers.ValidationError("Name exactly one rider: a student or a staff member.")
        if attrs["stop"].route_id != attrs["route"].pk:
            raise serializers.ValidationError({"stop": "That stop isn't on this route."})
        return attrs


class EndAssignmentSerializer(serializers.Serializer):
    on = serializers.DateField(required=False)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class TransportInvoicesSerializer(OwnedInput):
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())
    route = serializers.PrimaryKeyRelatedField(queryset=Route.objects.all(), required=False, allow_null=True)
    due_date = serializers.DateField(required=False)

    def validate_term(self, value):
        return self.own(value, "term")

    def validate_route(self, value):
        value = self.own(value, "route")
        if value is not None:
            _in_scope(self, value.campus)
        return value


# ---------------------------------------------------------------------------
# Trips
# ---------------------------------------------------------------------------
class TripRecordSerializer(serializers.ModelSerializer):
    rider_name = serializers.CharField(source="assignment.rider_name", read_only=True)
    student = serializers.IntegerField(source="assignment.student_id", read_only=True)
    staff = serializers.IntegerField(source="assignment.staff_id", read_only=True)
    stop_name = serializers.CharField(source="assignment.stop.name", read_only=True)
    date = serializers.DateField(source="trip.date", read_only=True)
    direction = serializers.CharField(source="trip.direction", read_only=True)
    route_name = serializers.CharField(source="trip.route.name", read_only=True)

    class Meta:
        model = TripRecord
        fields = ["id", "organization", "trip", "date", "direction", "route_name", "assignment", "student", "staff",
                  "rider_name", "stop_name", "status", "at", "note", "marked_by", "created_at", "updated_at"]
        read_only_fields = fields


class TripSerializer(serializers.ModelSerializer):
    route_name = serializers.CharField(source="route.name", read_only=True)
    vehicle_name = serializers.CharField(source="vehicle.name", read_only=True, default=None)
    roster = serializers.SerializerMethodField()

    class Meta:
        model = Trip
        fields = ["id", "organization", "route", "route_name", "date", "direction", "vehicle", "vehicle_name",
                  "driver", "assistant", "status", "completed_at", "opened_by", "note", "roster", "created_at",
                  "updated_at"]
        read_only_fields = fields

    def get_roster(self, obj) -> list[dict]:
        """Everyone expected on the trip, with their mark so far (or none)."""
        marks = {r.assignment_id: r for r in obj.records.all()}
        rows = []
        for a in services.riders_for(obj):
            mark = marks.get(a.pk)
            rows.append({"assignment": a.pk, "rider_name": a.rider_name, "student": a.student_id,
                         "staff": a.staff_id, "stop": a.stop_id, "stop_name": a.stop.name,
                         "status": mark.status if mark else None, "at": mark.at if mark else None})
        return rows


class TripListSerializer(TripSerializer):
    class Meta(TripSerializer.Meta):
        fields = [f for f in TripSerializer.Meta.fields if f != "roster"]
        read_only_fields = fields


class OpenTripSerializer(OwnedInput):
    route = serializers.PrimaryKeyRelatedField(queryset=Route.objects.select_related("driver__staff",
                                                                                      "assistant__staff"))
    date = serializers.DateField(required=False)
    direction = serializers.ChoiceField(choices=TripDirection.choices)

    def validate_route(self, value):
        return self.own(value, "route")


class BoardingEntrySerializer(OwnedLine):
    assignment = serializers.PrimaryKeyRelatedField(queryset=Assignment.objects.select_related("student", "staff"))
    status = serializers.ChoiceField(choices=BoardingStatus.choices)
    at = serializers.TimeField(required=False, allow_null=True)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_assignment(self, value):
        return self.own(value, "rider")


class BoardingMarksSerializer(serializers.Serializer):
    entries = BoardingEntrySerializer(many=True, allow_empty=False)


# ---------------------------------------------------------------------------
# Upkeep
# ---------------------------------------------------------------------------
class VehicleMaintenanceSerializer(OwnedSerializer):
    vehicle_name = serializers.CharField(source="vehicle.name", read_only=True)

    class Meta:
        model = Maintenance
        fields = ["id", "organization", "vehicle", "vehicle_name", "kind", "date", "odometer", "cost", "vendor",
                  "description", "next_due_on", "next_due_odometer", "recorded_by", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "recorded_by", "created_at", "updated_at"]

    def validate_vehicle(self, value):
        value = self.own(value, "vehicle")
        _in_scope(self, value.campus)
        return value

    def validate_cost(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("A cost can't be negative.")
        return value


class FuelLogSerializer(OwnedSerializer):
    vehicle_name = serializers.CharField(source="vehicle.name", read_only=True)

    class Meta:
        model = FuelLog
        fields = ["id", "organization", "vehicle", "vehicle_name", "date", "litres", "cost", "odometer", "note",
                  "recorded_by", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "recorded_by", "created_at", "updated_at"]

    def validate_vehicle(self, value):
        value = self.own(value, "vehicle")
        _in_scope(self, value.campus)
        return value

    def validate_litres(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be more than zero.")
        return value

    def validate_cost(self, value):
        if value < 0:
            raise serializers.ValidationError("A cost can't be negative.")
        return value
