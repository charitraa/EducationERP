from rest_framework import serializers

from core.common.serializers import ensure_unique_in_organization, target_organization_id
from modules.students.models import Student

from .models import (
    Award,
    AwardRule,
    Event,
    EventAttendance,
    EventCategory,
    EventParticipation,
    EventRegistration,
    PointEntry,
    PointRule,
    StudentAward,
    StudentPoints,
)


class OwnedModelSerializer(serializers.ModelSerializer):
    def own(self, obj, label):
        """Another organization's id is answered like a missing one."""
        if obj is not None and obj.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return obj


# ---------------------------------------------------------------------------
# Categories, events
# ---------------------------------------------------------------------------
class EventCategorySerializer(OwnedModelSerializer):
    class Meta:
        model = EventCategory
        fields = ["id", "organization", "code", "name", "description", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value


class EventSerializer(OwnedModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    organized_by_name = serializers.CharField(source="organized_by.full_name", read_only=True, default=None)
    is_over = serializers.BooleanField(read_only=True)
    registration_open = serializers.BooleanField(read_only=True)
    confirmed_count = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = ["id", "organization", "campus", "category", "category_name", "name", "description", "venue",
                  "start_at", "end_at", "status", "registration_mode", "registration_deadline", "capacity",
                  "confirmed_count", "organized_by", "organized_by_name", "is_over", "registration_open",
                  "cancelled_at", "cancelled_reason", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "status", "cancelled_at", "cancelled_reason", "created_at",
                            "updated_at"]

    def get_confirmed_count(self, obj) -> int:
        return obj.registrations.filter(status="confirmed").count()

    def validate_campus(self, value):
        return self.own(value, "campus")

    def validate_category(self, value):
        return self.own(value, "category")

    def validate_organized_by(self, value):
        return self.own(value, "staff member")

    def validate(self, attrs):
        instance = self.instance
        get = lambda k: attrs.get(k, getattr(instance, k, None) if instance else None)
        start, end = get("start_at"), get("end_at")
        if start and end and end < start:
            raise serializers.ValidationError({"end_at": "Must not be before the start."})
        if instance is not None and instance.status == "cancelled":
            raise serializers.ValidationError("This event is cancelled.")
        return attrs


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
class EventRegistrationSerializer(OwnedModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    event_name = serializers.CharField(source="event.name", read_only=True)

    class Meta:
        model = EventRegistration
        fields = ["id", "organization", "event", "event_name", "student", "student_name", "student_number",
                  "status", "note", "decided_by", "decided_at", "decision_note", "created_at"]
        read_only_fields = ["id", "organization", "status", "decided_by", "decided_at", "decision_note",
                            "created_at"]

    def validate_event(self, value):
        return self.own(value, "event")

    def validate_student(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown student.")
        return value


class RegisterStudentSerializer(serializers.Serializer):
    event = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all())
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all())
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_event(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown event.")
        return value

    def validate_student(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown student.")
        return value


class DecideRegistrationSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Attendance, participation
# ---------------------------------------------------------------------------
class EventAttendanceSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_number = serializers.CharField(source="student.student_number", read_only=True)

    class Meta:
        model = EventAttendance
        fields = ["id", "event", "student", "student_name", "student_number", "status", "checked_in_at",
                  "marked_by"]
        read_only_fields = fields


class AttendanceEntrySerializer(serializers.Serializer):
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all())
    status = serializers.ChoiceField(choices=["present", "absent"])


class MarkAttendanceSerializer(serializers.Serializer):
    entries = AttendanceEntrySerializer(many=True, allow_empty=False)

    def validate_entries(self, entries):
        org = target_organization_id(self)
        for entry in entries:
            if entry["student"].organization_id != org:
                raise serializers.ValidationError("Unknown student.")
        ids = [e["student"].pk for e in entries]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("A student is listed twice.")
        return entries


class EventParticipationSerializer(OwnedModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    event_name = serializers.CharField(source="event.name", read_only=True)

    class Meta:
        model = EventParticipation
        fields = ["id", "organization", "event", "event_name", "student", "student_name", "role", "position",
                  "remark", "recorded_by", "created_at"]
        read_only_fields = ["id", "organization", "recorded_by", "created_at"]
        # Not DRF's auto unique-together validator: this serializer only ever validates input for
        # services.record_participation(), which recognizes an existing (event, student, role) row
        # and updates it instead of raising — recording the same role again is meant to work.
        validators = []

    def validate_event(self, value):
        return self.own(value, "event")

    def validate_student(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown student.")
        return value


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------
class PointRuleSerializer(OwnedModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)

    class Meta:
        model = PointRule
        fields = ["id", "organization", "name", "category", "category_name", "source", "role", "points",
                  "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_category(self, value):
        return self.own(value, "category")

    def validate_points(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be greater than zero.")
        return value

    def validate(self, attrs):
        source = attrs.get("source", self.instance.source if self.instance else None)
        role = attrs.get("role", self.instance.role if self.instance else None)
        if source != "participation" and role:
            raise serializers.ValidationError({"role": "Only a participation rule can name a role."})
        return attrs


class PointEntrySerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)

    class Meta:
        model = PointEntry
        fields = ["id", "organization", "student", "student_name", "points", "reason", "rule", "event",
                  "awarded_by", "created_at"]
        read_only_fields = fields


class AwardPointsSerializer(serializers.Serializer):
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all())
    points = serializers.IntegerField()
    reason = serializers.CharField(max_length=255)

    def validate_student(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown student.")
        return value

    def validate_points(self, value):
        if value == 0:
            raise serializers.ValidationError("Must not be zero.")
        return value


class StudentPointsSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)

    class Meta:
        model = StudentPoints
        fields = ["student", "student_name", "total", "updated_at"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Awards
# ---------------------------------------------------------------------------
class AwardSerializer(OwnedModelSerializer):
    class Meta:
        model = Award
        fields = ["id", "organization", "kind", "code", "name", "description", "icon", "is_active", "created_at",
                  "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value


class AwardRuleSerializer(OwnedModelSerializer):
    award_name = serializers.CharField(source="award.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)

    class Meta:
        model = AwardRule
        fields = ["id", "organization", "award", "award_name", "threshold_kind", "threshold_value", "category",
                  "category_name", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_award(self, value):
        return self.own(value, "award")

    def validate_category(self, value):
        return self.own(value, "category")


class StudentAwardSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    award_name = serializers.CharField(source="award.name", read_only=True)
    award_kind = serializers.CharField(source="award.kind", read_only=True)

    class Meta:
        model = StudentAward
        fields = ["id", "organization", "student", "student_name", "award", "award_name", "award_kind", "rule",
                  "awarded_at", "ended_on", "note", "granted_by", "created_at"]
        read_only_fields = fields


class GrantAwardSerializer(serializers.Serializer):
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all())
    award = serializers.PrimaryKeyRelatedField(queryset=Award.objects.all())
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_student(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown student.")
        return value

    def validate_award(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown award.")
        return value


class CancelEventSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class RegisterSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="",
                                 help_text="Why they're applying, for an approval event.")
