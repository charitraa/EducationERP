from rest_framework import serializers

from core.common.serializers import ensure_unique_in_organization, ensure_unique_together, target_organization_id
from modules.academics.models import Term
from modules.staff.models import StaffMember
from modules.students.models import Student

from .models import (
    HOLDING,
    Allocation,
    Bed,
    Building,
    Complaint,
    ComplaintCategory,
    Floor,
    HostelRoom,
    RoomType,
)


class OwnedSerializer(serializers.ModelSerializer):
    def own(self, value, label):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return value


class OwnedInput(serializers.Serializer):
    """A plain input serializer that can check a related row's tenant."""

    def own(self, value, label):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return value


def _in_scope(serializer, campus):
    """Refuse a campus the caller's role doesn't cover, during validation, so
    the reply can't describe what's there (a taken bed, a resident)."""
    view = serializer.context.get("view")
    if campus is not None and hasattr(view, "check_campus_allowed"):
        view.check_campus_allowed(campus)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------
class BuildingSerializer(OwnedSerializer):
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    warden_name = serializers.CharField(source="warden.full_name", read_only=True, default=None)

    class Meta:
        model = Building
        fields = ["id", "organization", "campus", "campus_name", "code", "name", "gender", "warden", "warden_name",
                  "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_campus(self, value):
        value = self.own(value, "campus")
        if self.instance is not None and value.pk != self.instance.campus_id and self.instance.rooms.exists():
            raise serializers.ValidationError("This building has rooms, so its campus can't change.")
        return value

    def validate_warden(self, value):
        return self.own(value, "staff member")

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value


class FloorSerializer(OwnedSerializer):
    class Meta:
        model = Floor
        fields = ["id", "organization", "building", "number", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_building(self, value):
        value = self.own(value, "building")
        if self.instance is not None and value.pk != self.instance.building_id and self.instance.rooms.exists():
            raise serializers.ValidationError("This floor has rooms, so it can't move to another building.")
        _in_scope(self, value.campus)
        return value

    def validate(self, attrs):
        ensure_unique_together(self, attrs, ["building", "number"], "This building already has that floor.")
        return attrs


class RoomTypeSerializer(OwnedSerializer):
    fee_category_name = serializers.CharField(source="fee_category.name", read_only=True, default=None)

    class Meta:
        model = RoomType
        fields = ["id", "organization", "code", "name", "fee_per_term", "fee_category", "fee_category_name",
                  "description", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

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


class HostelRoomSerializer(OwnedSerializer):
    building_name = serializers.CharField(source="building.name", read_only=True)
    room_type_name = serializers.CharField(source="room_type.name", read_only=True)
    beds = serializers.SerializerMethodField()
    occupied = serializers.SerializerMethodField()

    class Meta:
        model = HostelRoom
        fields = ["id", "organization", "building", "building_name", "floor", "number", "room_type",
                  "room_type_name", "is_active", "note", "beds", "occupied", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def get_beds(self, obj) -> int:
        return len([b for b in obj.beds.all() if b.is_active])

    def get_occupied(self, obj) -> int:
        cached = getattr(obj, "occupied_beds", None)
        if cached is not None:
            return cached
        return Allocation.objects.filter(bed__room=obj, status__in=HOLDING).count()

    def validate_building(self, value):
        value = self.own(value, "building")
        if self.instance is not None and value.pk != self.instance.building_id and self.instance.beds.exists():
            raise serializers.ValidationError("This room has beds, so it can't move to another building.")
        _in_scope(self, value.campus)
        return value

    def validate_floor(self, value):
        return self.own(value, "floor")

    def validate_room_type(self, value):
        return self.own(value, "room type")

    def validate(self, attrs):
        building = attrs.get("building", getattr(self.instance, "building", None))
        floor = attrs.get("floor", getattr(self.instance, "floor", None))
        if building is not None and floor is not None and floor.building_id != building.pk:
            raise serializers.ValidationError({"floor": "That floor is in another building."})
        ensure_unique_together(self, attrs, ["building", "number"], "This building already has a room with that number.")
        return attrs


class BedSerializer(OwnedSerializer):
    room_label = serializers.CharField(source="room.__str__", read_only=True)
    occupant = serializers.SerializerMethodField()

    class Meta:
        model = Bed
        fields = ["id", "organization", "room", "room_label", "label", "is_active", "occupant", "created_at",
                  "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def get_occupant(self, obj) -> dict | None:
        holding = [a for a in obj.allocations.all() if a.status in HOLDING]
        if not holding:
            return None
        a = holding[0]
        return {"allocation": a.pk, "status": a.status, "name": a.occupant_name,
                "student": a.student_id, "staff": a.staff_id}

    def validate_room(self, value):
        value = self.own(value, "room")
        if self.instance is not None and value.pk != self.instance.room_id and self.instance.allocations.exists():
            raise serializers.ValidationError("This bed has been allocated, so it can't move to another room.")
        _in_scope(self, value.building.campus)
        return value

    def validate(self, attrs):
        ensure_unique_together(self, attrs, ["room", "label"], "This room already has a bed with that label.")
        return attrs


# ---------------------------------------------------------------------------
# Allocations
# ---------------------------------------------------------------------------
class AllocationSerializer(serializers.ModelSerializer):
    occupant_name = serializers.CharField(read_only=True)
    bed_label = serializers.CharField(source="bed.label", read_only=True)
    room = serializers.IntegerField(source="bed.room_id", read_only=True)
    room_number = serializers.CharField(source="bed.room.number", read_only=True)
    building = serializers.IntegerField(source="bed.room.building_id", read_only=True)
    building_name = serializers.CharField(source="bed.room.building.name", read_only=True)

    class Meta:
        model = Allocation
        fields = ["id", "organization", "bed", "bed_label", "room", "room_number", "building", "building_name",
                  "student", "staff", "occupant_name", "status", "start_date", "end_date", "checked_in_at",
                  "checked_out_at", "note", "end_note", "allocated_by", "created_at", "updated_at"]
        read_only_fields = fields


class AllocateSerializer(OwnedInput):
    bed = serializers.PrimaryKeyRelatedField(queryset=Bed.objects.select_related("room__building"))
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all(), required=False, allow_null=True)
    staff = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.all(), required=False, allow_null=True)
    start_date = serializers.DateField(required=False)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_bed(self, value):
        value = self.own(value, "bed")
        _in_scope(self, value.room.building.campus)
        return value

    def validate_student(self, value):
        return self.own(value, "student")

    def validate_staff(self, value):
        return self.own(value, "staff member")

    def validate(self, attrs):
        if (attrs.get("student") is None) == (attrs.get("staff") is None):
            raise serializers.ValidationError("Name exactly one occupant: a student or a staff member.")
        return attrs


class CheckOutSerializer(serializers.Serializer):
    on = serializers.DateField(required=False)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class HostelReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class MoveSerializer(OwnedInput):
    bed = serializers.PrimaryKeyRelatedField(queryset=Bed.objects.select_related("room__building"))
    on = serializers.DateField(required=False)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_bed(self, value):
        value = self.own(value, "bed")
        _in_scope(self, value.room.building.campus)
        return value


class HostelInvoicesSerializer(OwnedInput):
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())
    building = serializers.PrimaryKeyRelatedField(queryset=Building.objects.all(), required=False, allow_null=True)
    due_date = serializers.DateField(required=False)

    def validate_term(self, value):
        return self.own(value, "term")

    def validate_building(self, value):
        value = self.own(value, "building")
        if value is not None:
            _in_scope(self, value.campus)
        return value


# ---------------------------------------------------------------------------
# Complaints
# ---------------------------------------------------------------------------
class ComplaintSerializer(serializers.ModelSerializer):
    building_name = serializers.CharField(source="building.name", read_only=True)
    room_number = serializers.CharField(source="room.number", read_only=True, default=None)
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True, default=None)

    class Meta:
        model = Complaint
        fields = ["id", "organization", "building", "building_name", "room", "room_number", "raised_by",
                  "category", "title", "description", "status", "assigned_to", "assigned_to_name", "resolution",
                  "resolved_at", "resolved_by", "created_at", "updated_at"]
        read_only_fields = fields


class RaiseComplaintSerializer(OwnedInput):
    building = serializers.PrimaryKeyRelatedField(queryset=Building.objects.all())
    room = serializers.PrimaryKeyRelatedField(queryset=HostelRoom.objects.all(), required=False, allow_null=True)
    category = serializers.ChoiceField(choices=ComplaintCategory.choices, default=ComplaintCategory.OTHER)
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_building(self, value):
        value = self.own(value, "building")
        _in_scope(self, value.campus)
        return value

    def validate_room(self, value):
        return self.own(value, "room")

    def validate(self, attrs):
        room = attrs.get("room")
        if room is not None and room.building_id != attrs["building"].pk:
            raise serializers.ValidationError({"room": "That room isn't in this building."})
        return attrs


class MyComplaintSerializer(serializers.Serializer):
    """A resident's own complaint: the building and room are theirs."""

    category = serializers.ChoiceField(choices=ComplaintCategory.choices, default=ComplaintCategory.OTHER)
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")


class AssignComplaintSerializer(OwnedInput):
    staff = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.all())

    def validate_staff(self, value):
        return self.own(value, "staff member")


class ResolveComplaintSerializer(serializers.Serializer):
    resolution = serializers.CharField()

