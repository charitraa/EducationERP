"""Finance: what a student owes, and what they've paid.

One fee policy for the whole organization (like Phase 5's grading), split by
program, level and academic year — a **FeeStructure**'s **FeeStructureItem**s
say what each category (tuition, admission, exam fee, …) costs and how often
it's billed.

An **Invoice** is generated from that structure for one student's one term
(or one-time, e.g. admission), built from **InvoiceItem** lines: the fee
itself, any standing **Scholarship** or one-off discount (both negative
lines), and any fine (positive). Splitting it into due-dated
**Installment**s is optional and only changes when reminders fall due, never
what's owed.

**Payment**s are recorded against an invoice; a **Receipt** is issued for
each one. A **Refund** reverses a payment — it is never edited or deleted,
matching the rest of the platform: money already moved is history, and a
mistake is corrected by a new entry that says so, not by rewriting the old
one.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.common.models import OrganizationOwnedModel, TimeStampedModel

ALIVE = Q(deleted_at__isnull=True)
ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Fee structure
# ---------------------------------------------------------------------------
class FeeCategory(OrganizationOwnedModel):
    """A kind of charge: tuition, admission, exam fee, transport, library."""

    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "finance_fee_category"
        ordering = ["name", "pk"]
        verbose_name_plural = "fee categories"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_fee_category_code"),
        ]

    def __str__(self):
        return self.name


class FeeStructure(OrganizationOwnedModel):
    """What one program's one level costs in one academic year."""

    program = models.ForeignKey("academics.Program", on_delete=models.PROTECT, related_name="fee_structures")
    level = models.PositiveSmallIntegerField()
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT,
                                      related_name="fee_structures")
    name = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "finance_fee_structure"
        ordering = ["-academic_year__start_date", "program__name", "level"]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "level", "academic_year"], condition=ALIVE, name="uniq_fee_structure",
            ),
        ]

    def __str__(self):
        return self.name or f"{self.program.name} level {self.level} ({self.academic_year})"


class Frequency(models.TextChoices):
    ONE_TIME = "one_time", "Once (e.g. on admission)"
    PER_TERM = "per_term", "Every term"


class FeeStructureItem(TimeStampedModel):
    """One line of a fee structure: a category, an amount, how often."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="fee_structure_items"
    )
    fee_structure = models.ForeignKey(FeeStructure, on_delete=models.CASCADE, related_name="items")
    category = models.ForeignKey(FeeCategory, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.PER_TERM)

    class Meta:
        db_table = "finance_fee_structure_item"
        ordering = ["fee_structure_id", "category__name"]
        constraints = [
            models.UniqueConstraint(fields=["fee_structure", "category"], name="uniq_fee_structure_item"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="fee_structure_item_amount_positive"),
        ]

    def __str__(self):
        return f"{self.fee_structure}: {self.category.name}"


# ---------------------------------------------------------------------------
# Scholarships
# ---------------------------------------------------------------------------
class ScholarshipKind(models.TextChoices):
    PERCENTAGE = "percentage", "Percent off"
    FLAT = "flat", "Flat amount off"


class Scholarship(OrganizationOwnedModel):
    """A standing reduction a student can be granted: a merit scholarship,
    a staff-ward discount, a sibling discount. ``category`` narrows it to one
    fee category (e.g. tuition only); empty reduces every category."""

    name = models.CharField(max_length=200)
    category = models.ForeignKey(FeeCategory, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                 help_text="Empty: applies to every category.")
    kind = models.CharField(max_length=10, choices=ScholarshipKind.choices, default=ScholarshipKind.PERCENTAGE)
    value = models.DecimalField(max_digits=10, decimal_places=2,
                                help_text="A percentage (0-100) or a flat amount, per ``kind``.")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "finance_scholarship"
        ordering = ["name", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(value__gt=0), name="scholarship_value_positive"),
            models.CheckConstraint(
                condition=~Q(kind="percentage") | Q(value__lte=100), name="scholarship_percentage_in_range",
            ),
        ]

    def __str__(self):
        return self.name


class StudentScholarshipQuerySet(models.QuerySet):
    def on(self, day=None):
        from django.utils import timezone

        day = day or timezone.localdate()
        return self.filter(started_on__lte=day).filter(Q(ended_on__isnull=True) | Q(ended_on__gt=day))


class StudentScholarship(TimeStampedModel):
    """A scholarship granted to a student, from one date, until dropped or
    ended. History, like ``StudentElective``: an ended grant stays on record."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="student_scholarships"
    )
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="scholarships")
    scholarship = models.ForeignKey(Scholarship, on_delete=models.PROTECT, related_name="grants")
    started_on = models.DateField()
    ended_on = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    objects = StudentScholarshipQuerySet.as_manager()

    class Meta:
        db_table = "finance_student_scholarship"
        ordering = ["student_id", "-started_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "scholarship"], condition=Q(ended_on__isnull=True),
                name="uniq_open_student_scholarship",
            ),
            models.CheckConstraint(
                condition=Q(ended_on__isnull=True) | Q(ended_on__gt=F("started_on")),
                name="student_scholarship_dates_ordered",
            ),
        ]

    def __str__(self):
        return f"{self.student} — {self.scholarship.name}"


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ISSUED = "issued", "Issued"
    CANCELLED = "cancelled", "Cancelled"


class Invoice(TimeStampedModel):
    """One bill: a student's charges for one term (or a one-time fee),
    with what's been paid against it.

    ``paid_amount`` is kept in step with ``Payment`` and ``Refund`` inside
    the same transaction, the same way ``StaffAttendanceDay`` is kept in
    step with punches — worked out once, not recomputed by a background job.
    """

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="invoices")
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="invoices")
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="invoices")
    enrollment = models.ForeignKey("students.Enrollment", on_delete=models.PROTECT, related_name="invoices")
    fee_structure = models.ForeignKey(FeeStructure, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="invoices")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="invoices")
    term = models.ForeignKey("academics.Term", null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="invoices", help_text="Empty: a one-time invoice, e.g. admission.")
    invoice_number = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=InvoiceStatus.choices, default=InvoiceStatus.ISSUED,
                              db_index=True)
    issue_date = models.DateField()
    due_date = models.DateField()
    total = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "finance_invoice"
        ordering = ["-issue_date", "-pk"]
        indexes = [models.Index(fields=["organization", "campus", "student"])]
        constraints = [
            models.UniqueConstraint(fields=["organization", "invoice_number"], name="uniq_invoice_number"),
        ]

    def __str__(self):
        return self.invoice_number

    @property
    def balance(self) -> Decimal:
        return self.total - self.paid_amount

    @property
    def is_paid(self) -> bool:
        return self.status != InvoiceStatus.CANCELLED and self.balance <= 0

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone

        return (self.status == InvoiceStatus.ISSUED and self.balance > 0
                and self.due_date < timezone.localdate())


class InvoiceItemKind(models.TextChoices):
    FEE = "fee", "Fee"
    SCHOLARSHIP = "scholarship", "Scholarship"
    DISCOUNT = "discount", "Discount"
    FINE = "fine", "Fine"
    ADJUSTMENT = "adjustment", "Adjustment"


class InvoiceItem(TimeStampedModel):
    """One line of an invoice. ``amount`` is signed: positive adds to what's
    owed (a fee, a fine), negative reduces it (a scholarship, a discount)."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    category = models.ForeignKey(FeeCategory, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=12, choices=InvoiceItemKind.choices, default=InvoiceItemKind.FEE)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    scholarship = models.ForeignKey(Scholarship, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "finance_invoice_item"
        ordering = ["invoice_id", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(kind__in=["fee", "fine"], amount__gt=0)
                           | Q(kind__in=["scholarship", "discount"], amount__lt=0)
                           | Q(kind="adjustment")),
                name="invoice_item_amount_sign_matches_kind",
            ),
        ]

    def __str__(self):
        return f"{self.invoice}: {self.description}"


class Installment(TimeStampedModel):
    """A due-dated slice of an invoice's total, for reminders and late fees.
    Splitting an invoice this way never changes what it adds up to."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="installments")
    sequence = models.PositiveSmallIntegerField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()

    class Meta:
        db_table = "finance_installment"
        ordering = ["invoice_id", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["invoice", "sequence"], name="uniq_installment_sequence"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="installment_amount_positive"),
        ]

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone

        return self.due_date < timezone.localdate() and self.invoice.balance > 0


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    BANK = "bank", "Bank transfer"
    CHEQUE = "cheque", "Cheque"
    ONLINE = "online", "Online"
    OTHER = "other", "Other"


class Payment(TimeStampedModel):
    """Money received against one invoice. Append-only: a mistake is
    reversed by a ``Refund``, never edited here."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="payments")
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=10, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = models.CharField(max_length=100, blank=True, help_text="Cheque no., transaction id, …")
    note = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField()
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "finance_payment"
        ordering = ["-paid_at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive")]

    def __str__(self):
        return f"{self.amount} for {self.invoice}"

    @property
    def refunded_amount(self) -> Decimal:
        return sum((r.amount for r in self.refunds.all()), ZERO)

    @property
    def refundable_amount(self) -> Decimal:
        return self.amount - self.refunded_amount


class Receipt(TimeStampedModel):
    """The numbered proof of one payment."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="receipts")
    payment = models.OneToOneField(Payment, on_delete=models.CASCADE, related_name="receipt")
    receipt_number = models.CharField(max_length=40)
    issued_at = models.DateTimeField()

    class Meta:
        db_table = "finance_receipt"
        ordering = ["-issued_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "receipt_number"], name="uniq_receipt_number"),
        ]

    def __str__(self):
        return self.receipt_number


class Refund(TimeStampedModel):
    """Reverses some or all of one payment. Never edits or deletes it."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="refunds")
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=255)
    refunded_at = models.DateTimeField()
    refunded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "finance_refund"
        ordering = ["-refunded_at", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="refund_amount_positive")]

    def __str__(self):
        return f"Refund of {self.amount} on {self.payment}"
