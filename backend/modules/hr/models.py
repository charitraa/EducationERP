"""HR: who works here, on what terms, and when they're away.

HR extends Phase 2's ``StaffMember`` (the employee) rather than replacing it.
Departments are ``academics.Department`` — an office like Accounts is a
department with no programs — and holidays are the academic calendar's
campus-wide closures, which attendance already reads.

* **Position** — a structured job title ("Lecturer", "Accountant").
* **Contract** — the terms someone works under, from a date to a date: kind,
  position, department. History: a new contract follows the old one.
* **EmployeeProfile** — what HR and payroll need beyond the directory: PAN,
  bank account, tax status, fund numbers, emergency contact.
* **StaffDocument** — a record of a document held (citizenship, PAN card,
  certificates), with its number and expiry. The file itself waits for file
  storage; a link can be kept meanwhile.
* **FiscalYear** — the leave year, and payroll's tax year.
* **LeaveType**, **LeaveBalance**, **LeaveRequest** — leave with yearly
  quotas, carry-forward and an approval step. Approved leave is written into
  staff attendance through the attendance service.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.common.choices import Gender
from core.common.models import OrganizationOwnedModel, TimeStampedModel

ALIVE = Q(deleted_at__isnull=True)
ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Organization structure
# ---------------------------------------------------------------------------
class Position(OrganizationOwnedModel):
    """A job: "Lecturer", "Lab Assistant", "Accountant"."""

    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "hr_position"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_hr_position_code"),
        ]

    def __str__(self):
        return self.name


class ContractKind(models.TextChoices):
    PERMANENT = "permanent", "Permanent"
    PROBATION = "probation", "Probation"
    TEMPORARY = "temporary", "Temporary"
    CONTRACT = "contract", "Fixed-term contract"
    PART_TIME = "part_time", "Part-time"


class Contract(OrganizationOwnedModel):
    """The terms a staff member works under, from ``start_date`` until
    ``end_date`` (open-ended when empty). One at a time per person."""

    staff = models.ForeignKey("staff.StaffMember", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=20, choices=ContractKind.choices)
    position = models.ForeignKey(Position, null=True, blank=True, on_delete=models.PROTECT, related_name="contracts")
    department = models.ForeignKey("academics.Department", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Inclusive. Empty: open-ended.")
    probation_ends_on = models.DateField(null=True, blank=True)
    notice_period_days = models.PositiveSmallIntegerField(null=True, blank=True)
    reference = models.CharField(max_length=100, blank=True, help_text="Appointment letter number, etc.")
    notes = models.TextField(blank=True)
    ended_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "hr_contract"
        ordering = ["staff_id", "-start_date", "pk"]
        indexes = [models.Index(fields=["organization", "start_date"])]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                                   name="hr_contract_dates_ordered"),
        ]

    def __str__(self):
        return f"{self.staff} — {self.get_kind_display()} from {self.start_date}"


class TaxStatus(models.TextChoices):
    """Which tax slabs apply: a single person's or a couple's."""

    SINGLE = "single", "Single"
    MARRIED = "married", "Married / couple"


class EmployeeProfile(OrganizationOwnedModel):
    """HR's side of a staff member. ``tax_status`` picks the tax scheme
    (single or couple slabs)."""

    staff = models.ForeignKey("staff.StaffMember", on_delete=models.PROTECT, related_name="+")
    pan_number = models.CharField(max_length=20, blank=True)
    tax_status = models.CharField(max_length=10, choices=TaxStatus.choices, default=TaxStatus.SINGLE)
    bank_name = models.CharField(max_length=150, blank=True)
    bank_branch = models.CharField(max_length=150, blank=True)
    bank_account_name = models.CharField(max_length=150, blank=True)
    bank_account_number = models.CharField(max_length=50, blank=True)
    ssf_number = models.CharField(max_length=50, blank=True, help_text="Social Security Fund")
    pf_number = models.CharField(max_length=50, blank=True, help_text="Provident Fund")
    cit_number = models.CharField(max_length=50, blank=True, help_text="Citizen Investment Trust")
    citizenship_number = models.CharField(max_length=50, blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=32, blank=True)
    emergency_contact_relation = models.CharField(max_length=50, blank=True)

    class Meta:
        db_table = "hr_employee_profile"
        ordering = ["staff__first_name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["staff"], condition=ALIVE, name="uniq_hr_profile_per_staff"),
        ]

    def __str__(self):
        return f"Profile of {self.staff}"


class DocumentKind(models.TextChoices):
    CITIZENSHIP = "citizenship", "Citizenship"
    PAN = "pan", "PAN card"
    PASSPORT = "passport", "Passport"
    APPOINTMENT_LETTER = "appointment_letter", "Appointment letter"
    CERTIFICATE = "certificate", "Academic certificate"
    LICENSE = "license", "License (teaching, driving …)"
    CV = "cv", "CV"
    OTHER = "other", "Other"


class StaffDocument(OrganizationOwnedModel):
    staff = models.ForeignKey("staff.StaffMember", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=30, choices=DocumentKind.choices)
    title = models.CharField(max_length=200)
    number = models.CharField(max_length=100, blank=True)
    issued_by = models.CharField(max_length=150, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    file_url = models.URLField(blank=True, help_text="Until file storage exists: where the scan is kept.")
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "hr_staff_document"
        ordering = ["staff_id", "kind", "pk"]
        indexes = [models.Index(fields=["organization", "expires_on"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(expires_on__isnull=True) | Q(issued_on__isnull=True) | Q(expires_on__gte=F("issued_on")),
                name="hr_document_dates_ordered"),
        ]

    def __str__(self):
        return f"{self.title} ({self.staff})"


# ---------------------------------------------------------------------------
# Years
# ---------------------------------------------------------------------------
class FiscalYear(OrganizationOwnedModel):
    """The leave year and the tax year — in Nepal, Shrawan 1 to Asar end.
    AD dates; the BS name is free text, like academic years."""

    name = models.CharField(max_length=50)
    start_date = models.DateField()
    end_date = models.DateField(help_text="Inclusive.")

    class Meta:
        db_table = "hr_fiscal_year"
        ordering = ["-start_date", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], condition=ALIVE, name="uniq_hr_fiscal_year_name"),
            models.CheckConstraint(condition=Q(end_date__gt=F("start_date")), name="hr_fiscal_year_dates_ordered"),
        ]

    def __str__(self):
        return self.name

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


# ---------------------------------------------------------------------------
# Leave
# ---------------------------------------------------------------------------
class LeaveType(OrganizationOwnedModel):
    """Casual, sick, maternity, unpaid … ``annual_quota`` empty means no
    limit (still counted, never refused)."""

    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    is_paid = models.BooleanField(default=True, help_text="Unpaid leave is deducted by payroll.")
    annual_quota = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True,
                                       help_text="Days a fiscal year. Empty: unlimited.")
    carry_forward_max = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO,
                                            help_text="Unused days that move to next year, at most.")
    prorate_for_joiners = models.BooleanField(default=True,
                                              help_text="Someone joining mid-year gets a share of the quota.")
    allow_half_day = models.BooleanField(default=True)
    gender = models.CharField(max_length=20, choices=Gender.choices, blank=True,
                              help_text="Only staff of this gender (maternity, paternity). Empty: everyone.")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "hr_leave_type"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_hr_leave_type_code"),
            models.CheckConstraint(condition=Q(annual_quota__isnull=True) | Q(annual_quota__gte=0),
                                   name="hr_leave_quota_not_negative"),
            models.CheckConstraint(condition=Q(carry_forward_max__gte=0), name="hr_leave_carry_not_negative"),
        ]

    def __str__(self):
        return self.name


class LeaveBalance(TimeStampedModel):
    """One person's one leave type in one fiscal year. ``used`` is kept up
    to date by approvals and cancellations, like ``Invoice.paid_amount``."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    staff = models.ForeignKey("staff.StaffMember", on_delete=models.PROTECT, related_name="+")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="balances")
    fiscal_year = models.ForeignKey(FiscalYear, on_delete=models.PROTECT, related_name="leave_balances")
    entitled = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True,
                                   help_text="Empty: the type has no quota.")
    carried_forward = models.DecimalField(max_digits=6, decimal_places=1, default=ZERO)
    adjustment = models.DecimalField(max_digits=6, decimal_places=1, default=ZERO,
                                     help_text="Added or removed by hand (with a reason, in the audit log).")
    used = models.DecimalField(max_digits=6, decimal_places=1, default=ZERO)

    class Meta:
        db_table = "hr_leave_balance"
        ordering = ["staff_id", "leave_type__name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["staff", "leave_type", "fiscal_year"], name="uniq_hr_leave_balance"),
            models.CheckConstraint(condition=Q(used__gte=0), name="hr_leave_used_not_negative"),
        ]

    def __str__(self):
        return f"{self.staff} {self.leave_type} {self.fiscal_year}"

    @property
    def total(self):
        """Days available this year before anything is taken; None if unlimited."""
        if self.entitled is None:
            return None
        return self.entitled + self.carried_forward + self.adjustment


class LeaveStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class LeaveRequest(TimeStampedModel):
    """From applying, through a decision. Never deleted: a cancelled
    request stays on record."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    staff = models.ForeignKey("staff.StaffMember", on_delete=models.PROTECT, related_name="+")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="requests")
    fiscal_year = models.ForeignKey(FiscalYear, on_delete=models.PROTECT, related_name="leave_requests")
    start_date = models.DateField()
    end_date = models.DateField(help_text="Inclusive.")
    half_day = models.BooleanField(default=False)
    days = models.DecimalField(max_digits=5, decimal_places=1,
                               help_text="Working days taken: weekends and holidays don't count.")
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=LeaveStatus.choices, default=LeaveStatus.PENDING, db_index=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "hr_leave_request"
        ordering = ["-start_date", "-pk"]
        indexes = [models.Index(fields=["organization", "status"]), models.Index(fields=["staff", "start_date"])]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="hr_leave_dates_ordered"),
            models.CheckConstraint(condition=Q(half_day=False) | Q(end_date=F("start_date")),
                                   name="hr_leave_half_day_is_one_day"),
            models.CheckConstraint(condition=Q(days__gt=0), name="hr_leave_days_positive"),
        ]

    def __str__(self):
        return f"{self.staff} {self.leave_type} {self.start_date}–{self.end_date} ({self.status})"
