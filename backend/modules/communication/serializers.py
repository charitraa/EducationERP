from rest_framework import serializers

from core.accounts.models import User
from core.common.serializers import target_organization_id
from core.organizations.models import Campus
from modules.students.models import Student

from .models import Appointment, AppointmentSlot, Message, MessageThread


class OwnedModelSerializer(serializers.ModelSerializer):
    def own(self, obj, label):
        if obj is not None and obj.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return obj


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------
class MessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.get_full_name", read_only=True, default="")

    class Meta:
        model = Message
        fields = ["id", "thread", "sender", "sender_name", "body", "created_at"]
        read_only_fields = fields


class MessageThreadSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff_user.get_full_name", read_only=True, default="")
    other_name = serializers.CharField(source="other_user.get_full_name", read_only=True, default="")

    class Meta:
        model = MessageThread
        fields = ["id", "organization", "campus", "subject", "staff_user", "staff_name", "other_user", "other_name",
                  "is_closed", "last_message_at", "created_at"]
        read_only_fields = fields


class StartThreadSerializer(serializers.Serializer):
    other_user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    subject = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")

    def validate_other_user(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown user.")
        return value

    def validate_campus(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown campus.")
        return value


class SendMessageSerializer(serializers.Serializer):
    body = serializers.CharField()


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------
class AppointmentSlotSerializer(OwnedModelSerializer):
    staff_name = serializers.CharField(source="staff.full_name", read_only=True, default="")
    is_booked = serializers.SerializerMethodField()

    class Meta:
        model = AppointmentSlot
        fields = ["id", "organization", "campus", "staff", "staff_name", "starts_at", "ends_at", "location",
                  "is_cancelled", "is_booked", "created_at"]
        read_only_fields = ["id", "organization", "is_cancelled", "created_at"]

    def get_is_booked(self, obj) -> bool:
        return obj.appointments.exclude(status="cancelled").exists()

    def validate_staff(self, value):
        return self.own(value, "staff member")

    def validate_campus(self, value):
        return self.own(value, "campus")

    def validate(self, attrs):
        get = lambda k: attrs.get(k, getattr(self.instance, k, None) if self.instance else None)
        starts_at, ends_at = get("starts_at"), get("ends_at")
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError({"ends_at": "Must be after the start."})
        return attrs


class AppointmentSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="slot.staff.full_name", read_only=True, default="")
    starts_at = serializers.DateTimeField(source="slot.starts_at", read_only=True)
    ends_at = serializers.DateTimeField(source="slot.ends_at", read_only=True)
    requested_by_name = serializers.CharField(source="requested_by.get_full_name", read_only=True, default="")
    student_name = serializers.CharField(source="student.full_name", read_only=True, default=None)

    class Meta:
        model = Appointment
        fields = ["id", "organization", "slot", "staff_name", "starts_at", "ends_at", "requested_by",
                  "requested_by_name", "student", "student_name", "reason", "status", "cancelled_reason",
                  "decided_at", "created_at"]
        read_only_fields = fields


class BookAppointmentSerializer(serializers.Serializer):
    slot = serializers.PrimaryKeyRelatedField(queryset=AppointmentSlot.objects.filter(is_cancelled=False))
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all(), required=False, allow_null=True)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_slot(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown slot.")
        return value

    def validate_student(self, value):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown student.")
        return value


class CancelAppointmentSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)
