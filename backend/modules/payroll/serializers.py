from django.db import transaction
from rest_framework import serializers

from core.common.serializers import ensure_unique_in_organization, target_organization_id
from modules.hr.serializers import staff_in_scope
from core.organizations.models import Campus

from . import services
from .models import (
    Calculation,
    ComponentKind,
    PayComponent,
    PayrollAdjustment,
    PayrollRun,
    PayrollSettings,
    Payslip,
    PayslipLine,
    RunStatus,
    SalaryStructure,
    SalaryStructureLine,
    StaffSalary,
    StaffSalaryLine,
    TaxScheme,
    TaxSlab,
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


class ComponentLine(serializers.ModelSerializer):
    """A nested component + value; the tenant check goes through the root's view."""

    component_name = serializers.CharField(source="component.name", read_only=True)

    def validate_component(self, value):
        if value.organization_id != self.context["view"].get_target_organization_id():
            raise serializers.ValidationError("Unknown pay component.")
        return value

    def validate(self, attrs):
        component, value = attrs["component"], attrs["value"]
        if value < 0:
            raise serializers.ValidationError({"value": "Must be zero or more."})
        if component.calculation == Calculation.PERCENT_OF_BASIC and value > 100:
            raise serializers.ValidationError({"value": "A percentage, at most 100."})
        return attrs


def _no_duplicate_components(lines):
    ids = [line["component"].pk for line in lines]
    if len(ids) != len(set(ids)):
        raise serializers.ValidationError("Each component once.")
    return lines


class StaffFields(serializers.Serializer):
    staff_name = serializers.CharField(source="staff.full_name", read_only=True)
    employee_number = serializers.CharField(source="staff.employee_number", read_only=True)


# ---------------------------------------------------------------------------
# Settings, components, structures
# ---------------------------------------------------------------------------
class PayrollSettingsSerializer(OwnedSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)

    class Meta:
        model = PayrollSettings
        fields = ["id", "organization", "organization_name", "absence_basis", "deduct_half_days", "days_basis", "overtime_enabled",
                  "overtime_multiplier", "overtime_min_minutes", "default_day_minutes", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate(self, attrs):
        if self.instance is None and PayrollSettings.objects.filter(
                organization_id=target_organization_id(self)).exists():
            raise serializers.ValidationError("Settings already exist; update them instead.")
        if attrs.get("default_day_minutes") == 0:
            raise serializers.ValidationError({"default_day_minutes": "Must be more than zero."})
        return attrs


class PayComponentSerializer(OwnedSerializer):
    class Meta:
        model = PayComponent
        fields = ["id", "organization", "code", "name", "kind", "calculation", "is_taxable", "is_pre_tax",
                  "prorate_for_absence", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value

    def validate(self, attrs):
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        if attrs.get("is_pre_tax", getattr(self.instance, "is_pre_tax", False)) and kind != ComponentKind.DEDUCTION:
            raise serializers.ValidationError({"is_pre_tax": "Only a deduction is taken before tax."})
        return attrs


class SalaryStructureLineSerializer(ComponentLine):
    class Meta:
        model = SalaryStructureLine
        fields = ["id", "component", "component_name", "value"]


class SalaryStructureSerializer(OwnedSerializer):
    lines = SalaryStructureLineSerializer(many=True, required=False)

    class Meta:
        model = SalaryStructure
        fields = ["id", "organization", "code", "name", "description", "basic", "is_active", "lines",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value

    def validate_basic(self, value):
        if value < 0:
            raise serializers.ValidationError("Must be zero or more.")
        return value

    def validate_lines(self, value):
        return _no_duplicate_components(value)

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        structure = super().create(validated_data)
        SalaryStructureLine.objects.bulk_create([SalaryStructureLine(structure=structure, **line) for line in lines])
        return structure

    @transaction.atomic
    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        structure = super().update(instance, validated_data)
        if lines is not None:
            structure.lines.all().delete()
            SalaryStructureLine.objects.bulk_create(
                [SalaryStructureLine(structure=structure, **line) for line in lines])
        return structure


# ---------------------------------------------------------------------------
# Staff salaries
# ---------------------------------------------------------------------------
class StaffSalaryLineSerializer(ComponentLine):
    class Meta:
        model = StaffSalaryLine
        fields = ["id", "component", "component_name", "value"]


class StaffSalarySerializer(OwnedSerializer, StaffFields):
    lines = StaffSalaryLineSerializer(many=True, required=False)
    structure_name = serializers.CharField(source="structure.name", read_only=True)
    monthly_basic = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = StaffSalary
        fields = ["id", "organization", "staff", "staff_name", "employee_number", "structure", "structure_name",
                  "basic", "monthly_basic", "effective_from", "effective_to", "note", "lines", "assigned_by",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "effective_to", "assigned_by", "created_at", "updated_at"]

    def validate_staff(self, value):
        if self.instance is not None and value != self.instance.staff:
            raise serializers.ValidationError("Can't be changed.")
        return staff_in_scope(self, value)

    def validate_structure(self, value):
        return self.own(value, "salary structure")

    def validate_basic(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("Must be zero or more.")
        return value

    def validate_lines(self, value):
        return _no_duplicate_components(value)

    def validate(self, attrs):
        if self.instance is not None:
            if "effective_from" in attrs and attrs["effective_from"] != self.instance.effective_from:
                raise serializers.ValidationError(
                    {"effective_from": "Can't be changed; assign a new salary from the new date."})
            if services.salary_in_use(self.instance):
                raise serializers.ValidationError(
                    "Payslips were worked out from this salary; assign a new one instead.")
        return attrs

    def create(self, validated_data):
        validated_data.pop("organization_id", None)  # the staff member's, checked in validate_staff
        return services.assign_salary(by=self.context["request"].user, lines=validated_data.pop("lines", []),
                                      **validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        salary = super().update(instance, validated_data)
        if lines is not None:
            salary.lines.all().delete()
            StaffSalaryLine.objects.bulk_create([StaffSalaryLine(salary=salary, **line) for line in lines])
        return salary


# ---------------------------------------------------------------------------
# Tax
# ---------------------------------------------------------------------------
class TaxSlabSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxSlab
        fields = ["sequence", "upto", "rate"]
        read_only_fields = ["sequence"]

    def validate_rate(self, value):
        if not 0 <= value <= 100:
            raise serializers.ValidationError("A percentage, 0 to 100.")
        return value


class TaxSchemeSerializer(OwnedSerializer):
    slabs = TaxSlabSerializer(many=True)
    fiscal_year_name = serializers.CharField(source="fiscal_year.name", read_only=True)

    class Meta:
        model = TaxScheme
        fields = ["id", "organization", "fiscal_year", "fiscal_year_name", "tax_status", "name",
                  "female_rebate_percent", "pre_tax_cap_annual", "pre_tax_cap_fraction", "slabs", "created_at",
                  "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_fiscal_year(self, value):
        return self.own(value, "fiscal year")

    def validate_female_rebate_percent(self, value):
        if not 0 <= value <= 100:
            raise serializers.ValidationError("A percentage, 0 to 100.")
        return value

    def validate_pre_tax_cap_fraction(self, value):
        if value is not None and not 0 <= value <= 1:
            raise serializers.ValidationError("A share, 0 to 1.")
        return value

    def validate_slabs(self, value):
        if not value:
            raise serializers.ValidationError("At least one slab.")
        previous = None
        for number, slab in enumerate(value):
            upto, last = slab.get("upto"), number == len(value) - 1
            if upto is None and not last:
                raise serializers.ValidationError("Only the last slab can be open-ended.")
            if upto is not None and (upto <= 0 or (previous is not None and upto <= previous)):
                raise serializers.ValidationError("Slab limits must rise.")
            previous = upto
        if value[-1].get("upto") is not None:
            raise serializers.ValidationError("The last slab must be open-ended (no 'upto').")
        return value

    def validate(self, attrs):
        year = attrs.get("fiscal_year", getattr(self.instance, "fiscal_year", None))
        status = attrs.get("tax_status", getattr(self.instance, "tax_status", None))
        clash = TaxScheme.objects.filter(fiscal_year=year, tax_status=status)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({"tax_status": "This year already has a scheme for this status."})
        return attrs

    def _save_slabs(self, scheme, slabs):
        scheme.slabs.all().delete()
        TaxSlab.objects.bulk_create([TaxSlab(scheme=scheme, sequence=n + 1, upto=s.get("upto"), rate=s["rate"])
                                     for n, s in enumerate(slabs)])

    @transaction.atomic
    def create(self, validated_data):
        slabs = validated_data.pop("slabs")
        scheme = super().create(validated_data)
        self._save_slabs(scheme, slabs)
        return scheme

    @transaction.atomic
    def update(self, instance, validated_data):
        slabs = validated_data.pop("slabs", None)
        scheme = super().update(instance, validated_data)
        if slabs is not None:
            self._save_slabs(scheme, slabs)
        return scheme


# ---------------------------------------------------------------------------
# Runs and payslips
# ---------------------------------------------------------------------------
class PayrollRunSerializer(serializers.ModelSerializer):
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    fiscal_year_name = serializers.CharField(source="fiscal_year.name", read_only=True, default=None)
    totals = serializers.SerializerMethodField()

    class Meta:
        model = PayrollRun
        fields = ["id", "organization", "campus", "campus_name", "name", "period_start", "period_end",
                  "fiscal_year", "fiscal_year_name", "status", "notes", "created_by", "computed_at",
                  "approved_by", "approved_at", "paid_by", "paid_at", "payment_reference", "cancelled_reason",
                  "totals", "created_at", "updated_at"]
        read_only_fields = fields

    def get_totals(self, obj) -> dict:
        from django.db.models import Count, Sum

        totals = obj.payslips.aggregate(payslips=Count("pk"), gross_pay=Sum("gross_pay"), tax=Sum("tax"),
                                        total_deductions=Sum("total_deductions"), net_pay=Sum("net_pay"))
        count = totals.pop("payslips")
        return {"payslips": count, **{key: str(services.q(value or 0)) for key, value in totals.items()}}


class CreateRunSerializer(OwnedInput):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    name = serializers.CharField(max_length=100)
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_campus(self, value):
        return self.own(value, "campus")


class CancelRunSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class MarkPaidSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    paid_at = serializers.DateTimeField(required=False)


class PayslipLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayslipLine
        fields = ["id", "kind", "source", "component", "adjustment", "description", "amount", "is_taxable",
                  "is_pre_tax"]
        read_only_fields = fields


class PayslipListSerializer(serializers.ModelSerializer, StaffFields):
    run_name = serializers.CharField(source="run.name", read_only=True)
    run_status = serializers.CharField(source="run.status", read_only=True)

    class Meta:
        model = Payslip
        fields = ["id", "organization", "number", "run", "run_name", "run_status", "staff", "staff_name",
                  "employee_number", "basic", "gross_pay", "tax", "total_deductions", "net_pay"]
        read_only_fields = fields


class PayslipSerializer(PayslipListSerializer):
    lines = PayslipLineSerializer(many=True, read_only=True)
    period_start = serializers.DateField(source="run.period_start", read_only=True)
    period_end = serializers.DateField(source="run.period_end", read_only=True)

    class Meta(PayslipListSerializer.Meta):
        fields = PayslipListSerializer.Meta.fields + [
            "period_start", "period_end", "salary", "basis_days", "working_days", "worked_days",
            "paid_leave_days", "unpaid_leave_days", "absent_days", "not_employed_days", "overtime_minutes",
            "overtime_minutes_override", "taxable_income", "tax_scheme", "lines", "details", "created_at",
            "updated_at"]
        read_only_fields = fields


class SetOvertimeSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(min_value=0, allow_null=True,
                                       help_text="Null goes back to the count from attendance.")


class PayrollAdjustmentSerializer(OwnedSerializer, StaffFields):
    payslip_number = serializers.CharField(source="payslip.number", read_only=True, default=None)

    class Meta:
        model = PayrollAdjustment
        fields = ["id", "organization", "staff", "staff_name", "employee_number", "kind", "amount", "description",
                  "reason", "is_taxable", "corrects", "payslip", "payslip_number", "created_by", "created_at",
                  "updated_at"]
        read_only_fields = ["id", "organization", "payslip", "created_by", "created_at", "updated_at"]

    def validate_staff(self, value):
        return staff_in_scope(self, value)

    def validate_corrects(self, value):
        return self.own(value, "payslip")

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be greater than zero.")
        return value

    def validate(self, attrs):
        corrects = attrs.get("corrects")
        if corrects is not None:
            if corrects.staff_id != attrs["staff"].pk:
                raise serializers.ValidationError({"corrects": "That payslip is someone else's."})
            if corrects.run.status not in (RunStatus.APPROVED, RunStatus.PAID):
                raise serializers.ValidationError(
                    {"corrects": "Only an approved payslip needs a correction; recompute a draft instead."})
        return attrs

    def create(self, validated_data):
        return super().create({**validated_data, "created_by": self.context["request"].user})

