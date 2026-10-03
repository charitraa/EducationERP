"""Transport: vehicles and their papers, drivers, routes and stops, who rides
which route, each trip's roll, maintenance and fuel.

A **Route** belongs to one campus and has ordered **Stops**; a vehicle and a
crew (driver, optionally an assistant) are attached to it. An **Assignment**
puts one student or staff member on a route at a stop, from a date until an
end date — history, never deleted, like ``Enrollment``. A **Trip** is one run
of a route on a date in one direction (morning pickup, afternoon drop); its
**TripRecords** say who boarded. These are their own tables, separate from
the attendance module's class attendance.
"""
from django.db import models
from django.db.models import Q

from core.common.models import OrganizationOwnedModel

ALIVE = Q(deleted_at__isnull=True)


# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------
class VehicleKind(models.TextChoices):
    BUS = "bus", "Bus"
    MINIBUS = "minibus", "Minibus"
    VAN = "van", "Van"
    CAR = "car", "Car / jeep"
    OTHER = "other", "Other"


class Vehicle(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    registration_number = models.CharField(max_length=30, help_text="As on the bluebook, e.g. Ba 2 Kha 1234.")
    name = models.CharField(max_length=50, help_text="What everyone calls it, e.g. Bus 3.")
    kind = models.CharField(max_length=10, choices=VehicleKind.choices, default=VehicleKind.BUS)
    capacity = models.PositiveSmallIntegerField(help_text="Seats for riders.")
    make = models.CharField(max_length=50, blank=True)
    model = models.CharField(max_length=50, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "transport_vehicle"
        ordering = ["campus_id", "name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "registration_number"], condition=ALIVE,
                                    name="uniq_transport_vehicle_registration"),
            models.CheckConstraint(condition=Q(capacity__gt=0), name="transport_vehicle_capacity_positive"),
        ]

    def __str__(self):
        return f"{self.name} ({self.registration_number})"


class DocumentKind(models.TextChoices):
    BLUEBOOK = "bluebook", "Registration (bluebook)"
    INSURANCE = "insurance", "Insurance"
    ROUTE_PERMIT = "route_permit", "Route permit"
    POLLUTION = "pollution", "Pollution test"
    FITNESS = "fitness", "Fitness certificate"
    TAX = "tax", "Road tax"
    OTHER = "other", "Other"


class VehicleDocument(OrganizationOwnedModel):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=14, choices=DocumentKind.choices)
    number = models.CharField(max_length=100, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True, db_index=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "transport_vehicle_document"
        ordering = ["vehicle_id", "kind", "-expires_on"]
        constraints = [
            models.CheckConstraint(
                condition=Q(issued_on__isnull=True) | Q(expires_on__isnull=True)
                | Q(expires_on__gte=models.F("issued_on")),
                name="transport_document_dates_in_order"),
        ]


class CrewRole(models.TextChoices):
    DRIVER = "driver", "Driver"
    ASSISTANT = "assistant", "Assistant / conductor"


class Driver(OrganizationOwnedModel):
    """A staff member who crews vehicles. ``role`` decides whether they can be
    a route's driver or only its assistant."""

    staff = models.ForeignKey("staff.StaffMember", on_delete=models.PROTECT, related_name="+")
    role = models.CharField(max_length=10, choices=CrewRole.choices, default=CrewRole.DRIVER)
    license_number = models.CharField(max_length=50, blank=True)
    license_category = models.CharField(max_length=20, blank=True, help_text="e.g. B, C, C1")
    license_expires_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "transport_driver"
        ordering = ["staff__first_name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["staff"], condition=ALIVE, name="uniq_transport_driver_staff"),
        ]

    def __str__(self):
        return self.staff.full_name


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
class Route(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    code = models.SlugField(max_length=30)
    name = models.CharField(max_length=100)
    vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.PROTECT, related_name="routes")
    driver = models.ForeignKey(Driver, null=True, blank=True, on_delete=models.PROTECT, related_name="routes_driven")
    assistant = models.ForeignKey(Driver, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="routes_assisted")
    fee_per_term = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                       help_text="Per rider, per term; a stop can charge its own instead.")
    fee_category = models.ForeignKey("finance.FeeCategory", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", help_text="The invoice line's category. Needed to bill.")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "transport_route"
        ordering = ["campus_id", "code"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE,
                                    name="uniq_transport_route_code"),
            models.CheckConstraint(condition=Q(fee_per_term__gte=0), name="transport_route_fee_not_negative"),
        ]

    def __str__(self):
        return self.name


class Stop(OrganizationOwnedModel):
    route = models.ForeignKey(Route, on_delete=models.PROTECT, related_name="stops")
    sequence = models.PositiveSmallIntegerField(help_text="Order along the morning run, from 1.")
    name = models.CharField(max_length=100)
    landmark = models.CharField(max_length=255, blank=True)
    pickup_time = models.TimeField(null=True, blank=True)
    drop_time = models.TimeField(null=True, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    fee_per_term = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                       help_text="Overrides the route's fee for riders boarding here.")

    class Meta:
        db_table = "transport_stop"
        ordering = ["route_id", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["route", "sequence"], condition=ALIVE, name="uniq_transport_stop_seq"),
            models.CheckConstraint(condition=Q(fee_per_term__isnull=True) | Q(fee_per_term__gte=0),
                                   name="transport_stop_fee_not_negative"),
        ]

    def __str__(self):
        return f"{self.route.code} #{self.sequence} {self.name}"

    @property
    def fee(self):
        return self.fee_per_term if self.fee_per_term is not None else self.route.fee_per_term


class Direction(models.TextChoices):
    BOTH = "both", "Both ways"
    PICKUP = "pickup", "Morning pickup only"
    DROP = "drop", "Afternoon drop only"


class Assignment(OrganizationOwnedModel):
    """One rider on a route, boarding at ``stop``, from ``start_date`` to
    ``end_date`` (the last day; empty while they still ride). Exactly one of
    ``student`` / ``staff``; at most one open assignment per person."""

    route = models.ForeignKey(Route, on_delete=models.PROTECT, related_name="assignments")
    stop = models.ForeignKey(Stop, on_delete=models.PROTECT, related_name="assignments")
    student = models.ForeignKey("students.Student", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    staff = models.ForeignKey("staff.StaffMember", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    direction = models.CharField(max_length=6, choices=Direction.choices, default=Direction.BOTH)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    end_reason = models.CharField(max_length=255, blank=True)
    assigned_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        db_table = "transport_assignment"
        ordering = ["-start_date", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(student__isnull=False, staff__isnull=True) | Q(student__isnull=True, staff__isnull=False)),
                name="transport_assignment_exactly_one_rider"),
            models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")),
                                   name="transport_assignment_dates_in_order"),
            models.UniqueConstraint(fields=["student"], condition=ALIVE & Q(end_date__isnull=True),
                                    name="uniq_transport_student_open"),
            models.UniqueConstraint(fields=["staff"], condition=ALIVE & Q(end_date__isnull=True),
                                    name="uniq_transport_staff_open"),
        ]

    @property
    def rider(self):
        return self.student or self.staff

    @property
    def rider_name(self) -> str:
        rider = self.rider
        return rider.full_name if rider is not None else ""


# ---------------------------------------------------------------------------
# Trips
# ---------------------------------------------------------------------------
class TripDirection(models.TextChoices):
    PICKUP = "pickup", "Morning pickup"
    DROP = "drop", "Afternoon drop"


class TripStatus(models.TextChoices):
    OPEN = "open", "Open"
    COMPLETED = "completed", "Completed"


class Trip(OrganizationOwnedModel):
    """One run of a route. The vehicle and crew are copied from the route when
    it opens, so a later swap doesn't rewrite who drove that day."""

    route = models.ForeignKey(Route, on_delete=models.PROTECT, related_name="trips")
    date = models.DateField(db_index=True)
    direction = models.CharField(max_length=6, choices=TripDirection.choices)
    vehicle = models.ForeignKey(Vehicle, null=True, blank=True, on_delete=models.PROTECT, related_name="trips")
    driver = models.ForeignKey(Driver, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    assistant = models.ForeignKey(Driver, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=10, choices=TripStatus.choices, default=TripStatus.OPEN, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "transport_trip"
        ordering = ["-date", "route_id", "direction"]
        constraints = [
            models.UniqueConstraint(fields=["route", "date", "direction"], condition=ALIVE,
                                    name="uniq_transport_trip"),
        ]

    def __str__(self):
        return f"{self.route.code} {self.date} {self.direction}"


class BoardingStatus(models.TextChoices):
    BOARDED = "boarded", "Boarded"
    ABSENT = "absent", "Absent"


class TripRecord(OrganizationOwnedModel):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="records")
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name="records")
    status = models.CharField(max_length=8, choices=BoardingStatus.choices)
    at = models.TimeField(null=True, blank=True, help_text="When they got on (or off, on a drop).")
    note = models.CharField(max_length=255, blank=True)
    marked_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")

    class Meta:
        db_table = "transport_trip_record"
        ordering = ["trip_id", "assignment__stop__sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["trip", "assignment"], condition=ALIVE,
                                    name="uniq_transport_trip_record"),
        ]


# ---------------------------------------------------------------------------
# Upkeep
# ---------------------------------------------------------------------------
class MaintenanceKind(models.TextChoices):
    SERVICE = "service", "Regular service"
    REPAIR = "repair", "Repair"
    INSPECTION = "inspection", "Inspection"
    TYRES = "tyres", "Tyres"
    OTHER = "other", "Other"


class Maintenance(OrganizationOwnedModel):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="maintenance")
    kind = models.CharField(max_length=10, choices=MaintenanceKind.choices)
    date = models.DateField()
    odometer = models.PositiveIntegerField(null=True, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vendor = models.CharField(max_length=150, blank=True)
    description = models.CharField(max_length=255)
    next_due_on = models.DateField(null=True, blank=True)
    next_due_odometer = models.PositiveIntegerField(null=True, blank=True)
    recorded_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        db_table = "transport_maintenance"
        ordering = ["-date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(cost__isnull=True) | Q(cost__gte=0),
                                   name="transport_maintenance_cost_not_negative"),
        ]


class FuelLog(OrganizationOwnedModel):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="fuel_logs")
    date = models.DateField()
    litres = models.DecimalField(max_digits=8, decimal_places=2)
    cost = models.DecimalField(max_digits=12, decimal_places=2)
    odometer = models.PositiveIntegerField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        db_table = "transport_fuel_log"
        ordering = ["-date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(litres__gt=0), name="transport_fuel_litres_positive"),
            models.CheckConstraint(condition=Q(cost__gte=0), name="transport_fuel_cost_not_negative"),
        ]
