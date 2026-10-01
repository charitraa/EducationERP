"""Hostel: buildings, floors, rooms and beds, who sleeps where, and complaints.

A **Building** is at one campus; its rooms sit on **Floors**; each **room** has
**Beds** and a **RoomType** that carries the per-term fee. An **Allocation**
gives one bed to one student or staff member from a date: reserved, then
checked in, then checked out (or cancelled before check-in). Allocations are
history, never deleted, so the hostel register can be rebuilt for any date —
the same stance as ``Enrollment``. A room change is a check-out plus a new
allocation, not an edit.
"""
from django.db import models
from django.db.models import Q

from core.common.models import OrganizationOwnedModel

ALIVE = Q(deleted_at__isnull=True)


class BuildingGender(models.TextChoices):
    BOYS = "male", "Boys / men only"
    GIRLS = "female", "Girls / women only"
    MIXED = "mixed", "Anyone"


class Building(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    code = models.SlugField(max_length=30)
    name = models.CharField(max_length=100)
    gender = models.CharField(max_length=6, choices=BuildingGender.choices, default=BuildingGender.MIXED,
                              help_text="Who may be allocated a bed here.")
    warden = models.ForeignKey("staff.StaffMember", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "hostel_building"
        ordering = ["campus_id", "code"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE,
                                    name="uniq_hostel_building_code"),
        ]

    def __str__(self):
        return self.name


class Floor(OrganizationOwnedModel):
    building = models.ForeignKey(Building, on_delete=models.PROTECT, related_name="floors")
    number = models.SmallIntegerField(help_text="0 = ground floor; negative for basements.")
    name = models.CharField(max_length=50, blank=True)

    class Meta:
        db_table = "hostel_floor"
        ordering = ["building_id", "number"]
        constraints = [
            models.UniqueConstraint(fields=["building", "number"], condition=ALIVE, name="uniq_hostel_floor"),
        ]

    def __str__(self):
        return self.name or f"Floor {self.number}"


class RoomType(OrganizationOwnedModel):
    """Single, double, dormitory, AC… — the fee is per bed, per term."""

    code = models.SlugField(max_length=30)
    name = models.CharField(max_length=100)
    fee_per_term = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fee_category = models.ForeignKey("finance.FeeCategory", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", help_text="The invoice line's category. Needed to bill.")
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "hostel_room_type"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE,
                                    name="uniq_hostel_room_type_code"),
            models.CheckConstraint(condition=Q(fee_per_term__gte=0), name="hostel_room_type_fee_not_negative"),
        ]

    def __str__(self):
        return self.name


class HostelRoom(OrganizationOwnedModel):
    building = models.ForeignKey(Building, on_delete=models.PROTECT, related_name="rooms")
    floor = models.ForeignKey(Floor, on_delete=models.PROTECT, related_name="rooms")
    number = models.CharField(max_length=20)
    room_type = models.ForeignKey(RoomType, on_delete=models.PROTECT, related_name="rooms")
    is_active = models.BooleanField(default=True, db_index=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "hostel_room"
        ordering = ["building_id", "floor_id", "number"]
        constraints = [
            models.UniqueConstraint(fields=["building", "number"], condition=ALIVE, name="uniq_hostel_room"),
        ]

    def __str__(self):
        return f"{self.building.code}-{self.number}"


class Bed(OrganizationOwnedModel):
    room = models.ForeignKey(HostelRoom, on_delete=models.PROTECT, related_name="beds")
    label = models.CharField(max_length=10, help_text="A, B, 1, 2…")
    is_active = models.BooleanField(default=True, db_index=True,
                                    help_text="Off = out of service; no new allocations.")

    class Meta:
        db_table = "hostel_bed"
        ordering = ["room_id", "label"]
        constraints = [
            models.UniqueConstraint(fields=["room", "label"], condition=ALIVE, name="uniq_hostel_bed"),
        ]

    def __str__(self):
        return f"{self.room}/{self.label}"


class AllocationStatus(models.TextChoices):
    RESERVED = "reserved", "Reserved (not yet checked in)"
    CHECKED_IN = "checked_in", "Checked in"
    CHECKED_OUT = "checked_out", "Checked out"
    CANCELLED = "cancelled", "Cancelled before check-in"


HOLDING = [AllocationStatus.RESERVED, AllocationStatus.CHECKED_IN]


class Allocation(OrganizationOwnedModel):
    """One bed for one person, from ``start_date`` to ``end_date`` (the
    check-out day; empty while they still hold it). Exactly one of
    ``student`` / ``staff``. A bed, and a person, hold at most one
    reserved-or-checked-in allocation at a time."""

    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="allocations")
    student = models.ForeignKey("students.Student", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    staff = models.ForeignKey("staff.StaffMember", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    status = models.CharField(max_length=12, choices=AllocationStatus.choices, default=AllocationStatus.RESERVED,
                              db_index=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    end_note = models.CharField(max_length=255, blank=True, help_text="Why it ended or was cancelled.")
    allocated_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")

    class Meta:
        db_table = "hostel_allocation"
        ordering = ["-start_date", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(student__isnull=False, staff__isnull=True) | Q(student__isnull=True, staff__isnull=False)),
                name="hostel_allocation_exactly_one_occupant"),
            models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")),
                                   name="hostel_allocation_dates_in_order"),
            models.UniqueConstraint(fields=["bed"], condition=ALIVE & Q(status__in=HOLDING),
                                    name="uniq_hostel_bed_holder"),
            models.UniqueConstraint(fields=["student"], condition=ALIVE & Q(status__in=HOLDING),
                                    name="uniq_hostel_student_bed"),
            models.UniqueConstraint(fields=["staff"], condition=ALIVE & Q(status__in=HOLDING),
                                    name="uniq_hostel_staff_bed"),
        ]

    @property
    def occupant(self):
        return self.student or self.staff

    @property
    def occupant_name(self) -> str:
        occupant = self.occupant
        return occupant.full_name if occupant is not None else ""


class ComplaintCategory(models.TextChoices):
    MAINTENANCE = "maintenance", "Repairs and maintenance"
    CLEANLINESS = "cleanliness", "Cleanliness"
    FOOD = "food", "Food / mess"
    SECURITY = "security", "Safety and security"
    ROOMMATE = "roommate", "Roommates and conduct"
    OTHER = "other", "Other"


class ComplaintStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "Being handled"
    RESOLVED = "resolved", "Resolved"
    REJECTED = "rejected", "Rejected"


class Complaint(OrganizationOwnedModel):
    building = models.ForeignKey(Building, on_delete=models.PROTECT, related_name="complaints")
    room = models.ForeignKey(HostelRoom, null=True, blank=True, on_delete=models.PROTECT, related_name="complaints")
    raised_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    category = models.CharField(max_length=12, choices=ComplaintCategory.choices, default=ComplaintCategory.OTHER)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=ComplaintStatus.choices, default=ComplaintStatus.OPEN,
                              db_index=True)
    assigned_to = models.ForeignKey("staff.StaffMember", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    resolution = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        db_table = "hostel_complaint"
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.title
