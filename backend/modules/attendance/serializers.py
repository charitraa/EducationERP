import ipaddress

from django.utils import timezone
from rest_framework import serializers

from core.common.serializers import target_organization_id
from core.organizations.models import Campus
from modules.academics.models import Section
from modules.staff.models import StaffMember
from modules.students.models import Student
from modules.timetable.models import TimetableEntry

from . import qr
from .models import (
    AttendanceCorrection,
    AttendanceDevice,
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    BiometricIdentity,
    Punch,
    StaffAttendanceDay,
    StaffWorkSchedule,
    WorkSchedule,
)


def _own(serializer, obj, label):
    """Another organization's id is answered like a missing one."""
    if obj is not None and obj.organization_id != serializer.context["request"].user.organization_id:
        raise serializers.ValidationError(f"Unknown {label}.")
    return obj


class OwnedModelSerializer(serializers.ModelSerializer):
    def own(self, obj, label):
        if obj is not None and obj.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return obj


# ---------------------------------------------------------------------------
# Student sessions and records
# ---------------------------------------------------------------------------
class SessionSerializer(serializers.ModelSerializer):
    section_name = serializers.CharField(source="section.display_name", read_only=True)
    subject_name = serializers.CharField(
        source="timetable_entry.teaching_assignment.subject.name", read_only=True, default=None)
    start_time = serializers.TimeField(source="timetable_entry.period.start_time", read_only=True,
                                       default=None)
    teacher_name = serializers.CharField(source="teacher.full_name", read_only=True, default=None)

    class Meta:
        model = AttendanceSession
        fields = ["id", "organization", "campus", "section", "section_name", "date", "kind",
                  "timetable_entry", "subject_name", "start_time", "teacher", "teacher_name",
                  "status", "submitted_at", "submitted_by", "created_at"]
        read_only_fields = fields


class OpenSessionSerializer(serializers.Serializer):
    """Give the lesson (``timetable_entry``) for lesson attendance, or the
    ``section`` for a daily roll call."""

    date = serializers.DateField(required=False)
    section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.select_related(
        "program", "academic_year", "class_teacher"), required=False)
    timetable_entry = serializers.PrimaryKeyRelatedField(
        queryset=TimetableEntry.objects.select_related("teaching_assignment__section__program",
                                                        "teaching_assignment__section__academic_year"),
        required=False)

    def validate_section(self, section):
        return _own(self, section, "section")

    def validate_timetable_entry(self, entry):
        return _own(self, entry, "lesson")

    def validate(self, attrs):
        if bool(attrs.get("section")) == bool(attrs.get("timetable_entry")):
            raise serializers.ValidationError(
                "Give either timetable_entry (a lesson) or section (a daily roll call).")
        if "date" not in attrs:
            from .services import org_today

            attrs["date"] = org_today(self.context["request"].user.organization)
        return attrs


class MarkItemSerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    status = serializers.ChoiceField(choices=AttendanceStatus.choices)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")
    client_key = serializers.CharField(required=False, allow_blank=True, max_length=64, default="")
    recorded_at = serializers.DateTimeField(required=False)


class MarkSerializer(serializers.Serializer):
    records = MarkItemSerializer(many=True, required=False, default=list)
    rest = serializers.ChoiceField(
        choices=AttendanceStatus.choices, required=False,
        help_text="Everyone not yet marked gets this status, e.g. present.")

    def validate(self, attrs):
        if not attrs["records"] and "rest" not in attrs:
            raise serializers.ValidationError("Send records, rest, or both.")
        ids = [r["enrollment"] for r in attrs["records"]]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError({"records": "A student is listed twice."})
        return attrs


class SubmitSerializer(serializers.Serializer):
    rest = serializers.ChoiceField(
        choices=AttendanceStatus.choices, required=False,
        help_text="Status for anyone not yet marked. Without it, all must be marked.")


class SessionQRSerializer(serializers.Serializer):
    ttl = serializers.IntegerField(min_value=qr.MIN_TTL, max_value=qr.MAX_TTL,
                                   default=qr.DEFAULT_TTL, help_text="Seconds the code works for.")
    late_after = serializers.DateTimeField(required=False,
                                           help_text="Scans after this are marked late.")
    latitude = serializers.FloatField(required=False, min_value=-90, max_value=90)
    longitude = serializers.FloatField(required=False, min_value=-180, max_value=180)
    radius = serializers.IntegerField(required=False, min_value=10, max_value=2000,
                                      help_text="Metres from here a scan may come from.")

    def validate(self, attrs):
        location = [attrs.get(k) is not None for k in ("latitude", "longitude", "radius")]
        if any(location) and not all(location):
            raise serializers.ValidationError("Give latitude, longitude and radius together.")
        return attrs


class ScanSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=1000)
    latitude = serializers.FloatField(required=False, min_value=-90, max_value=90)
    longitude = serializers.FloatField(required=False, min_value=-180, max_value=180)
    device_id = serializers.CharField(required=False, allow_blank=True, max_length=128, default="")


class CorrectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceCorrection
        fields = ["old_status", "new_status", "reason", "corrected_by", "corrected_at"]
        read_only_fields = fields


class RecordSerializer(serializers.ModelSerializer):
    date = serializers.DateField(source="session.date", read_only=True)
    student = serializers.IntegerField(source="enrollment.student_id", read_only=True)
    student_name = serializers.CharField(source="enrollment.student.full_name", read_only=True)
    student_number = serializers.CharField(source="enrollment.student.student_number", read_only=True)
    subject_name = serializers.CharField(
        source="session.timetable_entry.teaching_assignment.subject.name", read_only=True,
        default=None)
    corrections = CorrectionSerializer(many=True, read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = ["id", "session", "date", "subject_name", "enrollment", "student", "student_name",
                  "student_number", "status", "source", "note", "marked_by", "recorded_at",
                  "created_at", "updated_at", "corrections"]
        read_only_fields = fields


class CorrectSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=AttendanceStatus.choices)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255, default="",
                                   help_text="Required once the session is submitted.")
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------
class WorkScheduleSerializer(OwnedModelSerializer):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())

    class Meta:
        model = WorkSchedule
        fields = ["id", "organization", "campus", "name", "start_time", "end_time", "grace_minutes",
                  "half_day_minutes", "weekdays", "is_default", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_campus(self, campus):
        return self.own(campus, "campus")

    def validate_weekdays(self, value):
        if not isinstance(value, list) or not value or any(
                not isinstance(d, int) or not 1 <= d <= 7 for d in value):
            raise serializers.ValidationError("A list of weekdays, 1 = Monday … 7 = Sunday.")
        return sorted(set(value))

    def validate(self, attrs):
        start = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"end_time": "Must be after the start."})
        campus = attrs.get("campus", getattr(self.instance, "campus", None))
        if attrs.get("is_default", getattr(self.instance, "is_default", False)):
            clash = WorkSchedule.objects.filter(campus=campus, is_default=True)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError({"is_default": "The campus already has a default."})
        return attrs


class StaffWorkScheduleSerializer(OwnedModelSerializer):
    staff = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.all())
    schedule = serializers.PrimaryKeyRelatedField(queryset=WorkSchedule.objects.all())
    staff_name = serializers.CharField(source="staff.full_name", read_only=True)

    class Meta:
        model = StaffWorkSchedule
        fields = ["id", "organization", "staff", "staff_name", "schedule", "created_at"]
        read_only_fields = ["id", "organization", "created_at"]

    def validate_staff(self, staff):
        return self.own(staff, "staff member")

    def validate_schedule(self, schedule):
        return self.own(schedule, "schedule")


class StaffDaySerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.full_name", read_only=True)

    class Meta:
        model = StaffAttendanceDay
        fields = ["id", "staff", "staff_name", "date", "status", "first_in", "last_out",
                  "worked_minutes", "is_override", "note", "set_by", "updated_at"]
        read_only_fields = fields


class StaffDayOverrideSerializer(serializers.Serializer):
    staff = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.select_related("campus"))
    date = serializers.DateField()
    status = serializers.ChoiceField(choices=StaffAttendanceDay.Status.choices)
    note = serializers.CharField(max_length=255, help_text="Why it's set by hand.")

    def validate_staff(self, staff):
        return _own(self, staff, "staff member")


class PunchSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source="staff.full_name", read_only=True, default=None)

    class Meta:
        model = Punch
        fields = ["id", "campus", "device", "pin", "staff", "staff_name", "student", "punched_at",
                  "direction", "source", "verify", "marked_by", "note", "received_at"]
        read_only_fields = fields


class ManualPunchSerializer(serializers.Serializer):
    staff = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.select_related("campus"))
    punched_at = serializers.DateTimeField()
    direction = serializers.ChoiceField(choices=Punch.Direction.choices, default=Punch.Direction.UNKNOWN)
    note = serializers.CharField(max_length=255, help_text="Why it's entered by hand.")

    def validate_staff(self, staff):
        return _own(self, staff, "staff member")

    def validate_punched_at(self, value):
        if value > timezone.now():
            raise serializers.ValidationError("Can't be in the future.")
        return value


class StaffQRSerializer(SessionQRSerializer):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    late_after = None

    def validate_campus(self, campus):
        return _own(self, campus, "campus")


class StaffScanSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=1000)
    latitude = serializers.FloatField(required=False, min_value=-90, max_value=90)
    longitude = serializers.FloatField(required=False, min_value=-180, max_value=180)


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------
class DeviceSerializer(OwnedModelSerializer):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())

    class Meta:
        model = AttendanceDevice
        fields = ["id", "organization", "campus", "name", "kind", "serial_number", "key_prefix",
                  "allowed_ips", "is_active", "last_seen_at", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "key_prefix", "last_seen_at", "created_at",
                            "updated_at"]

    def validate_campus(self, campus):
        return self.own(campus, "campus")

    def validate_serial_number(self, value):
        value = value.strip()
        clash = AttendanceDevice.objects.filter(serial_number=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            # Serials are global (a ZKTeco device names itself by it alone),
            # so this can't say whose it is.
            raise serializers.ValidationError("This serial number is already registered.")
        return value

    def validate_allowed_ips(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("A list of IP addresses.")
        try:
            return [str(ipaddress.ip_address(ip)) for ip in value]
        except (ValueError, TypeError):
            raise serializers.ValidationError("A list of IP addresses.")


class BiometricIdentitySerializer(OwnedModelSerializer):
    staff = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.select_related("campus"),
                                               required=False, allow_null=True)
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.select_related("campus"),
                                                 required=False, allow_null=True)
    person_name = serializers.SerializerMethodField()

    class Meta:
        model = BiometricIdentity
        fields = ["id", "organization", "pin", "staff", "student", "person_name", "created_at"]
        read_only_fields = ["id", "organization", "created_at"]

    def get_person_name(self, identity) -> str:
        return (identity.staff or identity.student).full_name

    def validate_staff(self, staff):
        return self.own(staff, "staff member")

    def validate_student(self, student):
        return self.own(student, "student")

    def validate_pin(self, value):
        value = value.strip()
        clash = BiometricIdentity.objects.filter(organization_id=target_organization_id(self), pin=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("This PIN is already mapped.")
        return value

    def validate(self, attrs):
        staff = attrs.get("staff", getattr(self.instance, "staff", None))
        student = attrs.get("student", getattr(self.instance, "student", None))
        if bool(staff) == bool(student):
            raise serializers.ValidationError("Give either staff or student.")
        return attrs
