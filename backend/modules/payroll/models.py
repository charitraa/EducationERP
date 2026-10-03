"""Payroll: what each staff member is paid, and the monthly run that pays it.

Kept apart from student finance: nothing here touches invoices.

* **PayComponent** — an allowance or a deduction (dearness allowance, PF,
  CIT, SSF), a fixed amount or a percentage of basic.
* **SalaryStructure** — a reusable grade: basic plus components.
  **StaffSalary** assigns one to a person from a date, optionally with their
  own basic and component values. History, like a scholarship grant: a new
  assignment ends the old one.
* **TaxScheme** — an organization's yearly income-tax slabs for single or
  couple filers, with an optional rebate for women and a cap on pre-tax
  (retirement) deductions. Nothing is hardcoded to one country's law.
* **PayrollRun** — one campus's pay period (explicit dates, so Nepali months
  work), computed into a **Payslip** per person from their salary, leave
  and attendance. Draft → approved (locked) → paid.
* **PayrollAdjustment** — a one-off earning or deduction (arrears, a bonus,
  an advance recovered, a correction to an approved payslip). Picked up by
  that person's next computed payslip; an approved payslip is never edited.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.common.models import OrganizationOwnedModel, TimeStampedModel
from modules.hr.models import TaxStatus

ALIVE = Q(deleted_at__isnull=True)
ZERO = Decimal("0")


def money(**kwargs):
    return models.DecimalField(max_digits=12, decimal_places=2, **kwargs)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
class AbsenceBasis(models.TextChoices):
    MARKED = "marked", "Only days marked absent"
    UNRECORDED = "unrecorded", "Any past working day without attendance"


class DaysBasis(models.TextChoices):
    WORKING = "working", "Working days in the period"
    CALENDAR = "calendar", "Calendar days in the period"


class PayrollSettings(OrganizationOwnedModel):
    """How an organization's payroll treats attendance. Without a row the
    defaults apply."""

    absence_basis = models.CharField(
        max_length=20, choices=AbsenceBasis.choices, default=AbsenceBasis.MARKED,
        help_text="'unrecorded' suits campuses where everyone punches in; 'marked' where they don't.")
    deduct_half_days = models.BooleanField(default=False, help_text="A half day costs half a day's pay.")
    days_basis = models.CharField(max_length=20, choices=DaysBasis.choices, default=DaysBasis.WORKING,
                                  help_text="A day's pay is the monthly amount divided by these.")
    overtime_enabled = models.BooleanField(default=True)
    overtime_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("1.50"))
    overtime_min_minutes = models.PositiveSmallIntegerField(
        default=30, help_text="Extra time on a day below this isn't counted.")
    default_day_minutes = models.PositiveSmallIntegerField(
        default=480, help_text="Length of a working day for staff with no work schedule.")

    class Meta:
        db_table = "payroll_settings"
        ordering = ["pk"]
        verbose_name_plural = "payroll settings"
        constraints = [
            models.UniqueConstraint(fields=["organization"], condition=ALIVE, name="uniq_payroll_settings"),
            models.CheckConstraint(condition=Q(overtime_multiplier__gt=0), name="payroll_overtime_multiplier_positive"),
        ]

    def __str__(self):
        return f"Payroll settings of {self.organization}"


# ---------------------------------------------------------------------------
# Components and structures
# ---------------------------------------------------------------------------
class ComponentKind(models.TextChoices):
    EARNING = "earning", "Earning (allowance)"
    DEDUCTION = "deduction", "Deduction"


class Calculation(models.TextChoices):
    FIXED = "fixed", "Fixed amount a month"
    PERCENT_OF_BASIC = "percent_of_basic", "Percentage of basic"


class PayComponent(OrganizationOwnedModel):
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=ComponentKind.choices)
    calculation = models.CharField(max_length=20, choices=Calculation.choices, default=Calculation.FIXED)
    is_taxable = models.BooleanField(default=True, help_text="Earnings: counts as taxable income.")
    is_pre_tax = models.BooleanField(
        default=False, help_text="Deductions: taken before tax (provident fund, CIT, SSF).")
    prorate_for_absence = models.BooleanField(
        default=True, help_text="Earnings: reduced for unpaid days, like basic.")
    is_active = models.BooleanField(default=True, db_index=True,
                                    help_text="Inactive components are left off new payslips.")

    class Meta:
        db_table = "payroll_component"
        ordering = ["kind", "name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_payroll_component"),
            models.CheckConstraint(condition=Q(kind="deduction") | Q(is_pre_tax=False),
                                   name="payroll_pre_tax_is_a_deduction"),
        ]

    def __str__(self):
        return self.name


class SalaryStructure(OrganizationOwnedModel):
    """A pay grade: "Lecturer A", "Office assistant"."""

    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    basic = money(help_text="Monthly basic salary.")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "payroll_salary_structure"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_salary_structure"),
            models.CheckConstraint(condition=Q(basic__gte=0), name="payroll_structure_basic_not_negative"),
        ]

    def __str__(self):
        return self.name


class SalaryStructureLine(TimeStampedModel):
    structure = models.ForeignKey(SalaryStructure, on_delete=models.CASCADE, related_name="lines")
    component = models.ForeignKey(PayComponent, on_delete=models.PROTECT, related_name="+")
    value = models.DecimalField(max_digits=12, decimal_places=2,
                                help_text="An amount, or a percentage for percent-of-basic components.")

    class Meta:
        db_table = "payroll_salary_structure_line"
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["structure", "component"], name="uniq_structure_component"),
            models.CheckConstraint(condition=Q(value__gte=0), name="payroll_structure_line_not_negative"),
        ]


class StaffSalary(TimeStampedModel):
    """A structure assigned to one person from ``effective_from``, until the
    next assignment (or ``effective_to``). ``basic`` and ``lines`` override
    the structure's for this person only."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    staff = models.ForeignKey("staff.StaffMember", on_delete=models.PROTECT, related_name="+")
    structure = models.ForeignKey(SalaryStructure, on_delete=models.PROTECT, related_name="assignments")
    basic = money(null=True, blank=True, help_text="This person's own basic. Empty: the structure's.")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True, help_text="Inclusive. Empty: until replaced.")
    note = models.CharField(max_length=255, blank=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        db_table = "payroll_staff_salary"
        ordering = ["staff_id", "-effective_from", "pk"]
        verbose_name_plural = "staff salaries"
        constraints = [
            models.UniqueConstraint(fields=["staff"], condition=Q(effective_to__isnull=True),
                                    name="uniq_open_staff_salary"),
            models.CheckConstraint(condition=Q(effective_to__isnull=True) | Q(effective_to__gte=F("effective_from")),
                                   name="payroll_staff_salary_dates_ordered"),
            models.CheckConstraint(condition=Q(basic__isnull=True) | Q(basic__gte=0),
                                   name="payroll_staff_basic_not_negative"),
        ]

    def __str__(self):
        return f"{self.staff} — {self.structure} from {self.effective_from}"

    @property
    def monthly_basic(self) -> Decimal:
        return self.basic if self.basic is not None else self.structure.basic


class StaffSalaryLine(TimeStampedModel):
    salary = models.ForeignKey(StaffSalary, on_delete=models.CASCADE, related_name="lines")
    component = models.ForeignKey(PayComponent, on_delete=models.PROTECT, related_name="+")
    value = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = "payroll_staff_salary_line"
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["salary", "component"], name="uniq_staff_salary_component"),
            models.CheckConstraint(condition=Q(value__gte=0), name="payroll_staff_line_not_negative"),
        ]


# ---------------------------------------------------------------------------
# Tax
# ---------------------------------------------------------------------------
class TaxScheme(OrganizationOwnedModel):
    """One fiscal year's slabs for one filing status."""

    fiscal_year = models.ForeignKey("hr.FiscalYear", on_delete=models.PROTECT, related_name="+")
    tax_status = models.CharField(max_length=10, choices=TaxStatus.choices)
    name = models.CharField(max_length=150, blank=True)
    female_rebate_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=ZERO, help_text="Taken off the tax of women employees.")
    pre_tax_cap_annual = money(null=True, blank=True,
                               help_text="Pre-tax deductions count up to this much a year. Empty: no cap.")
    pre_tax_cap_fraction = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True,
        help_text="…and up to this share of taxable income (e.g. 0.3333). Empty: no cap.")

    class Meta:
        db_table = "payroll_tax_scheme"
        ordering = ["-fiscal_year__start_date", "tax_status", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["fiscal_year", "tax_status"], condition=ALIVE,
                                    name="uniq_tax_scheme_per_year_status"),
            models.CheckConstraint(condition=Q(female_rebate_percent__gte=0) & Q(female_rebate_percent__lte=100),
                                   name="payroll_rebate_is_a_percentage"),
        ]

    def __str__(self):
        return self.name or f"{self.fiscal_year} ({self.get_tax_status_display()})"


class TaxSlab(TimeStampedModel):
    """Annual taxable income up to ``upto`` (after the slab before it) is
    taxed at ``rate`` percent. The last slab has no ``upto``."""

    scheme = models.ForeignKey(TaxScheme, on_delete=models.CASCADE, related_name="slabs")
    sequence = models.PositiveSmallIntegerField()
    upto = money(null=True, blank=True)
    rate = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        db_table = "payroll_tax_slab"
        ordering = ["scheme_id", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["scheme", "sequence"], name="uniq_tax_slab_sequence"),
            models.CheckConstraint(condition=Q(rate__gte=0) & Q(rate__lte=100), name="payroll_slab_rate_percentage"),
        ]


# ---------------------------------------------------------------------------
# Runs and payslips
# ---------------------------------------------------------------------------
class RunStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    CANCELLED = "cancelled", "Cancelled"


class PayrollRun(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=100, help_text="e.g. 'Baisakh 2083'.")
    period_start = models.DateField()
    period_end = models.DateField(help_text="Inclusive.")
    fiscal_year = models.ForeignKey("hr.FiscalYear", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+", help_text="Picks the tax scheme.")
    status = models.CharField(max_length=20, choices=RunStatus.choices, default=RunStatus.DRAFT, db_index=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    computed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=100, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "payroll_run"
        ordering = ["-period_start", "campus_id", "pk"]
        indexes = [models.Index(fields=["organization", "period_start"])]
        constraints = [
            models.CheckConstraint(condition=Q(period_end__gte=F("period_start")), name="payroll_run_dates_ordered"),
        ]

    def __str__(self):
        return f"{self.name} ({self.campus})"


class Payslip(TimeStampedModel):
    """One person's pay for one run, with the working-out kept beside it."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    staff = models.ForeignKey("staff.StaffMember", on_delete=models.PROTECT, related_name="+")
    salary = models.ForeignKey(StaffSalary, on_delete=models.PROTECT, related_name="payslips")
    number = models.CharField(max_length=32)
    basic = money()
    basis_days = models.PositiveSmallIntegerField(help_text="A day's pay is the monthly amount / this.")
    working_days = models.DecimalField(max_digits=5, decimal_places=1, help_text="Expected while employed.")
    worked_days = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO)
    paid_leave_days = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO)
    unpaid_leave_days = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO)
    absent_days = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO)
    not_employed_days = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO)
    overtime_minutes = models.PositiveIntegerField(default=0)
    overtime_minutes_override = models.PositiveIntegerField(null=True, blank=True,
                                                            help_text="Set by hand; used instead of the count.")
    gross_pay = money(default=ZERO)
    taxable_income = money(default=ZERO, help_text="This period's, after pre-tax deductions.")
    tax = money(default=ZERO)
    total_deductions = money(default=ZERO, help_text="Including tax.")
    net_pay = money(default=ZERO)
    tax_scheme = models.ForeignKey(TaxScheme, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    details = models.JSONField(default=dict, blank=True, help_text="Day-by-day working-out and warnings.")

    class Meta:
        db_table = "payroll_payslip"
        ordering = ["run_id", "staff__first_name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["run", "staff"], name="uniq_payslip_per_run"),
            models.UniqueConstraint(fields=["organization", "number"], name="uniq_payslip_number"),
        ]

    def __str__(self):
        return f"{self.number} {self.staff}"


class LineKind(models.TextChoices):
    EARNING = "earning", "Earning"
    DEDUCTION = "deduction", "Deduction"


class LineSource(models.TextChoices):
    BASIC = "basic", "Basic salary"
    COMPONENT = "component", "Salary component"
    UNPAID_DAYS = "unpaid_days", "Unpaid days"
    OVERTIME = "overtime", "Overtime"
    ADJUSTMENT = "adjustment", "Adjustment"
    TAX = "tax", "Income tax"


class PayslipLine(TimeStampedModel):
    """``amount`` is always positive; ``kind`` says which way it goes. Unpaid
    days are a deduction from earnings, so they lower taxable income."""

    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=20, choices=LineKind.choices)
    source = models.CharField(max_length=20, choices=LineSource.choices)
    component = models.ForeignKey(PayComponent, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    adjustment = models.ForeignKey("PayrollAdjustment", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    description = models.CharField(max_length=200)
    amount = money()
    is_taxable = models.BooleanField(default=False)
    is_pre_tax = models.BooleanField(default=False)

    class Meta:
        db_table = "payroll_payslip_line"
        ordering = ["payslip_id", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte=0), name="payroll_line_amount_not_negative"),
        ]


class PayrollAdjustment(TimeStampedModel):
    """A one-off amount for someone's next payslip. Once a payslip it went
    into is approved it's history; before that it can be withdrawn."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    staff = models.ForeignKey("staff.StaffMember", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=20, choices=LineKind.choices)
    amount = money()
    description = models.CharField(max_length=200)
    reason = models.CharField(max_length=255, blank=True)
    is_taxable = models.BooleanField(default=True, help_text="Earnings: counts as taxable income.")
    corrects = models.ForeignKey(Payslip, null=True, blank=True, on_delete=models.PROTECT, related_name="corrections",
                                 help_text="The approved payslip this puts right, if any.")
    payslip = models.ForeignKey(Payslip, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="adjustments", help_text="Where it was paid (or recovered).")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        db_table = "payroll_adjustment"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payroll_adjustment_amount_positive"),
        ]

    def __str__(self):
        return f"{self.staff} {self.kind} {self.amount}"
