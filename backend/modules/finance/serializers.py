from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from core.common.serializers import ensure_unique_in_organization, target_organization_id
from core.organizations.models import Campus
from modules.academics.models import Section, Term
from modules.students.models import Student

from .models import (
    FeeCategory,
    FeeStructure,
    FeeStructureItem,
    Installment,
    Invoice,
    InvoiceItem,
    InvoiceItemKind,
    Payment,
    PaymentMethod,
    Receipt,
    Refund,
    Scholarship,
    ScholarshipKind,
    StudentScholarship,
)


class OwnedModelSerializer(serializers.ModelSerializer):
    def own(self, obj, label):
        """Another organization's id is answered like a missing one."""
        if obj is not None and obj.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return obj


# ---------------------------------------------------------------------------
# Fee structure
# ---------------------------------------------------------------------------
class FeeCategorySerializer(OwnedModelSerializer):
    class Meta:
        model = FeeCategory
        fields = ["id", "organization", "code", "name", "description", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value


class FeeStructureItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = FeeStructureItem
        fields = ["id", "category", "category_name", "amount", "frequency"]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Must be greater than zero.")
        return value


class FeeStructureSerializer(OwnedModelSerializer):
    items = FeeStructureItemSerializer(many=True, required=False)
    program_name = serializers.CharField(source="program.name", read_only=True)

    class Meta:
        model = FeeStructure
        fields = ["id", "organization", "program", "program_name", "level", "academic_year", "name", "is_active",
                  "items", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_program(self, value):
        return self.own(value, "program")

    def validate_academic_year(self, value):
        return self.own(value, "academic year")

    def validate_items(self, items):
        for item in items:
            self.own(item["category"], "category")
        categories = [i["category"] for i in items]
        if len(categories) != len(set(c.pk for c in categories)):
            raise serializers.ValidationError("A category is listed twice.")
        return items

    def validate(self, attrs):
        instance = self.instance
        get = lambda k: attrs.get(k, getattr(instance, k, None) if instance else None)
        program, level, year = get("program"), get("level"), get("academic_year")
        if not program.has_level(level):
            raise serializers.ValidationError({"level": f"{program.name} has no such level."})
        clash = FeeStructure.objects.filter(program=program, level=level, academic_year=year)
        if instance is not None:
            clash = clash.exclude(pk=instance.pk)
        if clash.exists():
            raise serializers.ValidationError("A fee structure already exists for this program, level and year.")
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("items", [])
        structure = super().create(validated_data)
        FeeStructureItem.objects.bulk_create([
            FeeStructureItem(organization_id=structure.organization_id, fee_structure=structure, **i) for i in items
        ])
        return structure

    @transaction.atomic
    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        structure = super().update(instance, validated_data)
        if items is not None:
            if structure.invoices.exists():
                raise serializers.ValidationError(
                    {"items": "Invoices have already been generated from this structure; its items can't change."})
            structure.items.all().delete()
            FeeStructureItem.objects.bulk_create([
                FeeStructureItem(organization_id=structure.organization_id, fee_structure=structure, **i)
                for i in items
            ])
        return structure


# ---------------------------------------------------------------------------
# Scholarships
# ---------------------------------------------------------------------------
class ScholarshipSerializer(OwnedModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)

    class Meta:
        model = Scholarship
        fields = ["id", "organization", "name", "category", "category_name", "kind", "value", "is_active",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_category(self, value):
        return self.own(value, "category")

    def validate(self, attrs):
        kind = attrs.get("kind", self.instance.kind if self.instance else ScholarshipKind.PERCENTAGE)
        value = attrs.get("value", self.instance.value if self.instance else None)
        if value is not None and value <= 0:
            raise serializers.ValidationError({"value": "Must be greater than zero."})
        if kind == ScholarshipKind.PERCENTAGE and value is not None and value > 100:
            raise serializers.ValidationError({"value": "A percentage can't be over 100."})
        return attrs


class StudentScholarshipSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    scholarship_name = serializers.CharField(source="scholarship.name", read_only=True)

    class Meta:
        model = StudentScholarship
        fields = ["id", "organization", "student", "student_name", "scholarship", "scholarship_name", "started_on",
                  "ended_on", "reason", "granted_by", "created_at"]
        read_only_fields = ["id", "organization", "ended_on", "granted_by", "created_at"]

    def validate_student(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown student.")
        return value

    def validate_scholarship(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown scholarship.")
        return value

    def validate(self, attrs):
        student = attrs.get("student", self.instance.student if self.instance else None)
        scholarship = attrs.get("scholarship", self.instance.scholarship if self.instance else None)
        if StudentScholarship.objects.filter(student=student, scholarship=scholarship,
                                             ended_on__isnull=True).exists():
            raise serializers.ValidationError("This student already has this scholarship.")
        return attrs


class EndScholarshipSerializer(serializers.Serializer):
    ended_on = serializers.DateField()


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
class InvoiceItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)

    class Meta:
        model = InvoiceItem
        fields = ["id", "category", "category_name", "kind", "description", "amount", "scholarship", "added_by",
                  "created_at"]
        read_only_fields = fields


class InstallmentSerializer(serializers.ModelSerializer):
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Installment
        fields = ["id", "sequence", "amount", "due_date", "is_overdue"]
        read_only_fields = ["id", "sequence", "is_overdue"]


class InvoiceSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    term_name = serializers.CharField(source="term.name", read_only=True, default=None)
    balance = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    is_paid = serializers.BooleanField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    items = InvoiceItemSerializer(many=True, read_only=True)
    installments = InstallmentSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = ["id", "organization", "campus", "student", "student_name", "student_number", "enrollment",
                  "fee_structure", "academic_year", "term", "term_name", "source", "invoice_number", "status",
                  "issue_date", "due_date", "total", "paid_amount", "balance", "is_paid", "is_overdue", "cancelled_at",
                  "cancelled_reason", "note", "items", "installments", "created_at", "updated_at"]
        read_only_fields = fields


class InvoiceListSerializer(InvoiceSerializer):
    class Meta(InvoiceSerializer.Meta):
        fields = [f for f in InvoiceSerializer.Meta.fields if f not in ("items", "installments")]
        read_only_fields = fields


class GenerateTermInvoicesSerializer(serializers.Serializer):
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())
    section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.all(), required=False)
    due_date = serializers.DateField(required=False, help_text="Default: 15 days after the invoice is generated.")

    def validate_term(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown term.")
        return value

    def validate_section(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown section.")
        return value


class GenerateOneTimeInvoiceSerializer(serializers.Serializer):
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all())
    due_date = serializers.DateField(required=False)

    def validate_student(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown student.")
        return value


class AddInvoiceItemSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=[InvoiceItemKind.DISCOUNT, InvoiceItemKind.FINE,
                                            InvoiceItemKind.ADJUSTMENT])
    description = serializers.CharField(max_length=255)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    category = serializers.PrimaryKeyRelatedField(queryset=FeeCategory.objects.all(), required=False,
                                                  allow_null=True)

    def validate_category(self, value):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown category.")
        return value

    def validate(self, attrs):
        if attrs["kind"] in (InvoiceItemKind.DISCOUNT,) and attrs["amount"] > 0:
            attrs["amount"] = -attrs["amount"]
        if attrs["kind"] == InvoiceItemKind.FINE and attrs["amount"] <= 0:
            raise serializers.ValidationError({"amount": "A fine must be greater than zero."})
        if attrs["kind"] == InvoiceItemKind.DISCOUNT and attrs["amount"] >= 0:
            raise serializers.ValidationError({"amount": "A discount must be greater than zero."})
        return attrs


class CancelInvoiceSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class InstallmentInputSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    due_date = serializers.DateField()


class SetInstallmentsSerializer(serializers.Serializer):
    installments = InstallmentInputSerializer(many=True, allow_empty=False)


class AssessLateFeesSerializer(serializers.Serializer):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all(), required=False)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, min_value=Decimal("0.01"))
    percentage = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, min_value=Decimal("0.01"),
                                          max_value=Decimal("100"))
    grace_days = serializers.IntegerField(default=0, min_value=0)
    category = serializers.PrimaryKeyRelatedField(queryset=FeeCategory.objects.all(), required=False,
                                                  allow_null=True)

    def validate_category(self, value):
        if value is not None and value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown category.")
        return value

    def validate_campus(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown campus.")
        return value

    def validate(self, attrs):
        if ("amount" in attrs) == ("percentage" in attrs):
            raise serializers.ValidationError("Give either an amount or a percentage, not both.")
        return attrs


# ---------------------------------------------------------------------------
# Payments, receipts, refunds
# ---------------------------------------------------------------------------
class PaymentSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="invoice.student.full_name", read_only=True)
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    refundable_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    has_receipt = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = ["id", "organization", "campus", "invoice", "invoice_number", "student_name", "amount", "method",
                  "reference", "note", "paid_at", "received_by", "refundable_amount", "has_receipt", "created_at"]
        read_only_fields = fields

    def get_has_receipt(self, obj) -> bool:
        return hasattr(obj, "receipt")


class RecordPaymentSerializer(serializers.Serializer):
    invoice = serializers.PrimaryKeyRelatedField(queryset=Invoice.objects.all())

    def validate_invoice(self, value):
        if value.organization_id != target_organization_id(self):
            raise serializers.ValidationError("Unknown invoice.")
        return value
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    paid_at = serializers.DateTimeField(required=False)

    def validate_paid_at(self, value):
        from django.utils import timezone

        if value > timezone.now():
            raise serializers.ValidationError("Can't be in the future.")
        return value


class RefundSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="payment.invoice.invoice_number", read_only=True)

    class Meta:
        model = Refund
        fields = ["id", "organization", "payment", "invoice_number", "amount", "reason", "refunded_at",
                  "refunded_by", "created_at"]
        read_only_fields = fields


class RequestRefundSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    reason = serializers.CharField(max_length=255)


class ReceiptSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="payment.invoice.invoice_number", read_only=True)
    student_name = serializers.CharField(source="payment.invoice.student.full_name", read_only=True)
    amount = serializers.DecimalField(source="payment.amount", max_digits=10, decimal_places=2, read_only=True)
    method = serializers.CharField(source="payment.method", read_only=True)

    class Meta:
        model = Receipt
        fields = ["id", "organization", "payment", "receipt_number", "invoice_number", "student_name", "amount",
                  "method", "issued_at"]
        read_only_fields = fields
