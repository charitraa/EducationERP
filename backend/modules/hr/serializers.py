from rest_framework import serializers

from core.common.serializers import ensure_unique_in_organization, target_organization_id
from core.organizations.models import Campus
from modules.staff.models import StaffMember

from . import services
from .models import (
    Contract,
    EmployeeProfile,
    FiscalYear,
    LeaveBalance,
    LeaveRequest,
    LeaveType,
    Position,
    StaffDocument,
)


class OwnedSerializer(serializers.ModelSerializer):
    def own(self, value, label):
        """Another organization's id is answered like a missing one."""
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return value


class OwnedInput(serializers.Serializer):
    def own(self, value, label):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return value


class StaffFields(serializers.Serializer):
    staff_name = serializers.CharField(source="staff.full_name", read_only=True)
    employee_number = serializers.CharField(source="staff.employee_number", read_only=True)


def staff_in_scope(serializer, value):
    """Another organization's staff member is unknown; one at a campus the
    caller's role doesn't cover is refused before any other check, so the
    reply can't describe their records (e.g. an overlapping contract)."""
    value = serializer.own(value, "staff member")
    view = serializer.context.get("view")
    if value is not None and hasattr(view, "check_campus_allowed"):
        view.check_campus_allowed(value.campus)
    return value


def _staff_fixed(serializer, value):
    """The person a record is about doesn't change; make a new record instead."""
    if serializer.instance is not None and value != serializer.instance.staff:
        raise serializers.ValidationError("Can't be changed.")
    return staff_in_scope(serializer, value)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------
class PositionSerializer(OwnedSerializer):
    class Meta:
        model = Position
        fields = ["id", "organization", "code", "name", "description", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value


class ContractSerializer(OwnedSerializer, StaffFields):
    position_name = serializers.CharField(source="position.name", read_only=True, default=None)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)

    class Meta:
        model = Contract
        fields = ["id", "organization", "staff", "staff_name", "employee_number", "kind", "position",
                  "position_name", "department", "department_name", "start_date", "end_date", "probation_ends_on",
                  "notice_period_days", "reference", "notes", "ended_reason", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "ended_reason", "created_at", "updated_at"]

    def validate_staff(self, value):
        return _staff_fixed(self, value)

    def validate_position(self, value):
        return self.own(value, "position")

    def validate_department(self, value):
        return self.own(value, "department")

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        start, end, staff = get("start_date"), get("end_date"), get("staff")
        if end is not None and end < start:
            raise serializers.ValidationError({"end_date": "Must not be before the start date."})
        probation = get("probation_ends_on")
        if probation is not None and (probation < start or (end is not None and probation > end)):
            raise serializers.ValidationError({"probation_ends_on": "Must fall within the contract."})
        if staff.joined_on and start < staff.joined_on:
            raise serializers.ValidationError({"start_date": "Must not be before the staff member joined."})
        clash = services.overlapping_contracts(staff, start, end, exclude=self.instance).first()
        if clash is not None:
            raise serializers.ValidationError(
                {"start_date": f"Overlaps another contract ({clash.start_date}–{clash.end_date or 'open'}); "
                               "end that one first."})
        return attrs


class EndContractSerializer(serializers.Serializer):
    end_date = serializers.DateField()
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class EmployeeProfileSerializer(OwnedSerializer, StaffFields):
    class Meta:
        model = EmployeeProfile
        fields = ["id", "organization", "staff", "staff_name", "employee_number", "pan_number", "tax_status",
                  "bank_name", "bank_branch", "bank_account_name", "bank_account_number", "ssf_number",
                  "pf_number", "cit_number", "citizenship_number", "emergency_contact_name",
                  "emergency_contact_phone", "emergency_contact_relation", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_staff(self, value):
        value = _staff_fixed(self, value)
        taken = EmployeeProfile.objects.filter(staff=value)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise serializers.ValidationError("This staff member already has a profile.")
        return value


class StaffDocumentSerializer(OwnedSerializer, StaffFields):
    class Meta:
        model = StaffDocument
        fields = ["id", "organization", "staff", "staff_name", "employee_number", "kind", "title", "number",
                  "issued_by", "issued_on", "expires_on", "file_url", "notes", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_staff(self, value):
        return _staff_fixed(self, value)

    def validate(self, attrs):
        issued = attrs.get("issued_on", getattr(self.instance, "issued_on", None))
        expires = attrs.get("expires_on", getattr(self.instance, "expires_on", None))
        if issued and expires and expires < issued:
            raise serializers.ValidationError({"expires_on": "Must not be before the issue date."})
        return attrs


# ---------------------------------------------------------------------------
# Years and leave setup
# ---------------------------------------------------------------------------
class FiscalYearSerializer(OwnedSerializer):
    class Meta:
        model = FiscalYear
        fields = ["id", "organization", "name", "start_date", "end_date", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_name(self, value):
        ensure_unique_in_organization(self, "name", value)
        return value

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if end <= start:
            raise serializers.ValidationError({"end_date": "Must be after the start date."})
        if self.instance is not None and (start, end) != (self.instance.start_date, self.instance.end_date):
            if services.fiscal_year_in_use(self.instance):
                raise serializers.ValidationError(
                    {"start_date": "Leave has been recorded in this year; its dates can't change."})
        clash = services.overlapping_fiscal_years(target_organization_id(self), start, end,
                                                  exclude=self.instance).first()
        if clash is not None:
            raise serializers.ValidationError({"start_date": f"Overlaps {clash.name}."})
        return attrs


class LeaveTypeSerializer(OwnedSerializer):
    class Meta:
        model = LeaveType
        fields = ["id", "organization", "code", "name", "is_paid", "annual_quota", "carry_forward_max",
                  "prorate_for_joiners", "allow_half_day", "gender", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value

    def validate_annual_quota(self, value):
        if value is not None and (value < 0 or value * 2 != int(value * 2)):
            raise serializers.ValidationError("Must be zero or more, in half days.")
        return value

    def validate_carry_forward_max(self, value):
        if value < 0:
            raise serializers.ValidationError("Must be zero or more.")
        return value

    def validate(self, attrs):
        if self.instance is not None and "is_paid" in attrs and attrs["is_paid"] != self.instance.is_paid:
            if self.instance.requests.exists():
                raise serializers.ValidationError(
                    {"is_paid": "Leave of this type has been taken; add a new type instead."})
        return attrs


class LeaveBalanceSerializer(serializers.ModelSerializer, StaffFields):
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)
    fiscal_year_name = serializers.CharField(source="fiscal_year.name", read_only=True)
    total = serializers.DecimalField(max_digits=6, decimal_places=1, read_only=True, allow_null=True)
    pending = serializers.SerializerMethodField()
    available = serializers.SerializerMethodField()

    class Meta:
        model = LeaveBalance
        fields = ["id", "organization", "staff", "staff_name", "employee_number", "leave_type", "leave_type_name",
                  "fiscal_year", "fiscal_year_name", "entitled", "carried_forward", "adjustment", "total", "used",
                  "pending", "available", "created_at", "updated_at"]
        read_only_fields = fields

    def get_pending(self, obj) -> str:
        return str(services.pending_days(obj.staff, obj.leave_type, obj.fiscal_year))

    def get_available(self, obj) -> str | None:
        left = services.available(obj)
        return None if left is None else str(left)


class AdjustBalanceSerializer(serializers.Serializer):
    delta = serializers.DecimalField(max_digits=5, decimal_places=1)
    reason = serializers.CharField(max_length=255)

    def validate_delta(self, value):
        if value == 0 or value * 2 != int(value * 2):
            raise serializers.ValidationError("A non-zero number of half days.")
        return value


class OpenBalancesSerializer(OwnedInput):
    fiscal_year = serializers.PrimaryKeyRelatedField(queryset=FiscalYear.objects.all())
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all(), required=False, allow_null=True)

    def validate_fiscal_year(self, value):
        return self.own(value, "fiscal year")

    def validate_campus(self, value):
        return self.own(value, "campus")


# ---------------------------------------------------------------------------
# Leave requests
# ---------------------------------------------------------------------------
class LeaveRequestSerializer(serializers.ModelSerializer, StaffFields):
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)
    is_paid = serializers.BooleanField(source="leave_type.is_paid", read_only=True)
    fiscal_year_name = serializers.CharField(source="fiscal_year.name", read_only=True)

    class Meta:
        model = LeaveRequest
        fields = ["id", "organization", "staff", "staff_name", "employee_number", "leave_type", "leave_type_name",
                  "is_paid", "fiscal_year", "fiscal_year_name", "start_date", "end_date", "half_day", "days",
                  "reason", "status", "applied_by", "decided_by", "decided_at", "decision_note", "cancelled_by",
                  "cancelled_at", "created_at", "updated_at"]
        read_only_fields = fields


class ApplyLeaveSerializer(OwnedInput):
    """For oneself (``/leave-requests/me/``)."""

    leave_type = serializers.PrimaryKeyRelatedField(queryset=LeaveType.objects.all())
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    half_day = serializers.BooleanField(required=False, default=False)
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_leave_type(self, value):
        return self.own(value, "leave type")


class CreateLeaveRequestSerializer(ApplyLeaveSerializer):
    """HR, on a staff member's behalf."""

    staff = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.all())

    def validate_staff(self, value):
        return staff_in_scope(self, value)


class DecideLeaveSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class RejectLeaveSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=255)

