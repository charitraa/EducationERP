"""Payroll rules: salary assignments, tax, and computing a run.

Reads HR (leave, profiles, fiscal years) and attendance only through their
selectors; never writes to either.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.organizations.models import Organization
from core.permissions.selectors import campus_ids_with_permission
from modules.attendance import selectors as attendance_selectors
from modules.hr import selectors as hr_selectors
from modules.hr.services import fiscal_year_on
from modules.notifications.services import notify
from modules.staff.models import StaffMember

from .models import (
    ZERO,
    AbsenceBasis,
    Calculation,
    ComponentKind,
    DaysBasis,
    LineKind,
    LineSource,
    PayComponent,
    PayrollAdjustment,
    PayrollRun,
    PayrollSettings,
    Payslip,
    PayslipLine,
    RunStatus,
    StaffSalary,
    StaffSalaryLine,
    TaxScheme,
    TaxStatus,
)

MODULE = "payroll"
VIEW, MANAGE, APPROVE = "payroll.view", "payroll.manage", "payroll.approve"
CENT = Decimal("0.01")
HALF = Decimal("0.5")
MAX_PERIOD_DAYS = 32
WORKED = ("present", "late", "on_duty")
ONE_OFF = (LineSource.OVERTIME, LineSource.ADJUSTMENT)


def q(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def holds(user, code: str, campus_id) -> bool:
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


def ensure_campus_allowed(user, code: str, campus_id) -> None:
    if not holds(user, code, campus_id):
        raise PermissionDeniedError("Your role does not cover this campus for this action.", code="wrong_campus")


def settings_for(organization_id: int) -> PayrollSettings:
    """The organization's settings, or unsaved defaults."""
    return (PayrollSettings.objects.filter(organization_id=organization_id).first()
            or PayrollSettings(organization_id=organization_id))


# ---------------------------------------------------------------------------
# Salary assignments
# ---------------------------------------------------------------------------
def salary_in_use(salary: StaffSalary) -> bool:
    return salary.payslips.exists()


def assign_salary(*, staff, structure, effective_from, basic=None, lines=(), note="", by=None) -> StaffSalary:
    """Give ``staff`` a salary from ``effective_from``; the one before ends the
    day before. Refused if it would start inside an earlier assignment's
    history rather than after it."""
    with transaction.atomic():
        StaffMember.objects.select_for_update().get(pk=staff.pk)
        later = StaffSalary.objects.filter(staff=staff, effective_from__gte=effective_from).first()
        if later is not None:
            raise ConflictError(f"A salary from {later.effective_from} is already on record; a new one must "
                                "start after it.", code="not_after_current")
        current = StaffSalary.objects.filter(staff=staff).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=effective_from)).first()
        if current is not None:
            current.effective_to = effective_from - timedelta(days=1)
            current.save(update_fields=["effective_to", "updated_at"])
        salary = StaffSalary.objects.create(
            organization_id=staff.organization_id, staff=staff, structure=structure, basic=basic,
            effective_from=effective_from, note=note, assigned_by=by,
        )
        StaffSalaryLine.objects.bulk_create(
            [StaffSalaryLine(salary=salary, component=line["component"], value=line["value"]) for line in lines])
        log(AuditLog.Action.CREATE, instance=salary, module=MODULE, actor=by)
    return salary


def remove_salary(salary: StaffSalary, *, by=None) -> None:
    """Delete an assignment no payslip used. The one it ended the day before
    it started is open again, as if it had never been made."""
    with transaction.atomic():
        if salary_in_use(salary):
            raise ConflictError("Payslips were worked out from this salary.", code="in_use")
        if StaffSalary.objects.filter(staff=salary.staff, effective_from__gt=salary.effective_from).exists():
            raise ConflictError("A later salary follows this one; remove that first.", code="not_latest")
        previous = StaffSalary.objects.filter(
            staff=salary.staff, effective_to=salary.effective_from - timedelta(days=1)).first()
        log(AuditLog.Action.DELETE, instance=salary, module=MODULE, actor=by)
        salary.delete()
        if previous is not None and salary.effective_to is None:
            previous.effective_to = None
            previous.save(update_fields=["effective_to", "updated_at"])


def salary_for_period(staff, start, end) -> StaffSalary | None:
    """The latest assignment in force at any point of [start, end]."""
    return (StaffSalary.objects.filter(staff=staff, effective_from__lte=end)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=start))
            .select_related("structure").prefetch_related("lines__component", "structure__lines__component")
            .order_by("-effective_from").first())


def component_values(salary: StaffSalary) -> list[tuple[PayComponent, Decimal]]:
    """The structure's components, with this person's own values on top."""
    values = {line.component_id: (line.component, line.value) for line in salary.structure.lines.all()}
    for line in salary.lines.all():
        values[line.component_id] = (line.component, line.value)
    return [pair for pair in values.values() if pair[0].is_active and pair[0].deleted_at is None]


def component_amount(component: PayComponent, value: Decimal, basic: Decimal) -> Decimal:
    if component.calculation == Calculation.PERCENT_OF_BASIC:
        return q(basic * value / 100)
    return q(value)


# ---------------------------------------------------------------------------
# Tax
# ---------------------------------------------------------------------------
def annual_tax(scheme: TaxScheme, taxable: Decimal) -> Decimal:
    tax, floor = ZERO, ZERO
    for slab in scheme.slabs.order_by("sequence"):
        if taxable <= floor:
            break
        ceiling = slab.upto if slab.upto is not None else taxable
        band = min(taxable, ceiling) - floor
        if band > 0:
            tax += band * slab.rate / 100
        floor = ceiling
    return tax


def scheme_for(staff, fiscal_year) -> TaxScheme | None:
    if fiscal_year is None:
        return None
    profile = hr_selectors.profile_for(staff)
    status = profile.tax_status if profile is not None else TaxStatus.SINGLE
    return (TaxScheme.objects.filter(fiscal_year=fiscal_year, tax_status=status).prefetch_related("slabs").first())


def monthly_tax(scheme: TaxScheme | None, staff, *, regular: Decimal, one_off: Decimal = ZERO,
                pre_tax: Decimal = ZERO) -> tuple[Decimal, Decimal]:
    """(tax this period, taxable income this period).

    Regular pay is projected to a year (×12), less pre-tax deductions within
    the scheme's caps, and taxed by the slabs; a twelfth is withheld. One-off
    pay (overtime, bonuses, arrears) is taxed on top of that year at the
    marginal rate, all in this period — projecting a bonus ×12 would
    overstate the tax."""
    annual_regular = regular * 12
    allowed = pre_tax * 12
    if scheme is not None and scheme.pre_tax_cap_annual is not None:
        allowed = min(allowed, scheme.pre_tax_cap_annual)
    if scheme is not None and scheme.pre_tax_cap_fraction is not None:
        allowed = min(allowed, (annual_regular + one_off) * scheme.pre_tax_cap_fraction)
    base = max(ZERO, annual_regular - allowed)
    taxable = q(base / 12 + one_off)
    if scheme is None:
        return ZERO, max(ZERO, taxable)
    tax_regular = annual_tax(scheme, base)
    tax_one_off = annual_tax(scheme, base + one_off) - tax_regular
    tax = tax_regular / 12 + tax_one_off
    if scheme.female_rebate_percent and staff.gender == "female":
        tax -= tax * scheme.female_rebate_percent / 100
    return q(tax), max(ZERO, taxable)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def validate_period(*, organization_id, campus, start, end, exclude=None):
    if end < start:
        raise ServiceError("The period can't end before it starts.", code="dates_reversed")
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise ServiceError(f"A pay period is at most {MAX_PERIOD_DAYS} days (one month).", code="period_too_long")
    year = fiscal_year_on(organization_id, start)
    if year is not None and end > year.end_date:
        raise ServiceError(f"The period crosses the fiscal year's end ({year.end_date}).",
                           code="spans_fiscal_years")
    clash = PayrollRun.objects.filter(campus=campus, period_start__lte=end, period_end__gte=start).exclude(
        status=RunStatus.CANCELLED)
    if exclude is not None:
        clash = clash.exclude(pk=exclude.pk)
    clash = clash.first()
    if clash is not None:
        raise ConflictError(f"Overlaps the run '{clash.name}' ({clash.period_start}–{clash.period_end}).",
                            code="overlaps")
    return year


def create_run(*, campus, name, period_start, period_end, notes="", by=None) -> PayrollRun:
    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=campus.organization_id)
        year = validate_period(organization_id=campus.organization_id, campus=campus, start=period_start,
                               end=period_end)
        run = PayrollRun.objects.create(organization_id=campus.organization_id, campus=campus, name=name,
                                        period_start=period_start, period_end=period_end, fiscal_year=year,
                                        notes=notes, created_by=by)
        log(AuditLog.Action.CREATE, instance=run, module=MODULE, actor=by)
    return run


def _lock_run(run: PayrollRun) -> PayrollRun:
    return PayrollRun.objects.select_for_update().select_related("campus").get(pk=run.pk)


def _ensure_draft(run: PayrollRun) -> None:
    if run.status != RunStatus.DRAFT:
        raise ConflictError(f"This run is {run.get_status_display().lower()}; its payslips are locked.",
                            code="not_draft")


def _next_numbers(organization_id: int, count: int) -> list[str]:
    """The next ``count`` payslip numbers of the organization."""
    Organization.objects.select_for_update().get(pk=organization_id)
    last = (Payslip.objects.filter(organization_id=organization_id).order_by("-number")
            .values_list("number", flat=True).first())
    start = int(last[2:]) + 1 if last else 1
    return [f"PS{n:06d}" for n in range(start, start + count)]


def run_staff(run: PayrollRun):
    """The campus's staff employed at some point in the period."""
    return (StaffMember.objects.filter(organization_id=run.organization_id, campus=run.campus)
            .filter(Q(joined_on__isnull=True) | Q(joined_on__lte=run.period_end))
            .filter(Q(left_on__isnull=True) | Q(left_on__gt=run.period_start)))


def compute_run(run: PayrollRun, *, by=None) -> dict:
    """(Re)build every payslip of a draft run. Adjustments and overtime set
    by hand on the old payslips are kept."""
    with transaction.atomic():
        run = _lock_run(run)
        _ensure_draft(run)
        overrides = dict(run.payslips.filter(overtime_minutes_override__isnull=False)
                         .values_list("staff_id", "overtime_minutes_override"))
        run.payslips.all().delete()  # releases their adjustments (SET_NULL)
        settings = settings_for(run.organization_id)
        skipped, plans = [], []
        for staff in run_staff(run).order_by("first_name", "last_name", "pk"):
            elsewhere = (Payslip.objects.filter(staff=staff, run__period_start__lte=run.period_end,
                                                run__period_end__gte=run.period_start)
                         .exclude(run__status=RunStatus.CANCELLED).exclude(run=run).select_related("run").first())
            if elsewhere is not None:
                skipped.append({"staff": staff.pk, "staff_name": staff.full_name, "reason": "already_paid",
                                "detail": f"On payslip {elsewhere.number} ({elsewhere.run.name})."})
                continue
            salary = salary_for_period(staff, run.period_start, run.period_end)
            if salary is None:
                skipped.append({"staff": staff.pk, "staff_name": staff.full_name, "reason": "no_salary",
                                "detail": "No salary assigned for this period."})
                continue
            plans.append((staff, salary))
        numbers = _next_numbers(run.organization_id, len(plans))
        for (staff, salary), number in zip(plans, numbers):
            build_payslip(run, staff, salary, number=number, settings=settings,
                          overtime_override=overrides.get(staff.pk))
        run.computed_at = timezone.now()
        run.save(update_fields=["computed_at", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=run, module=MODULE, actor=by,
            changes={"computed_at": {"before": None, "after": run.computed_at.isoformat()}},
            metadata={"payslips": len(plans), "skipped": len(skipped)})
    return {"payslips": len(plans), "skipped": skipped}


def _count_days(staff, run, settings):
    """Attendance and leave, day by day, for the period."""
    start, end = run.period_start, run.period_end
    today = timezone.localdate()
    full = attendance_selectors.staff_working_days(staff, start, end, employed_only=False)
    expected = attendance_selectors.staff_working_days(staff, start, end)
    if settings.days_basis == DaysBasis.CALENDAR:
        basis = (end - start).days + 1
        employed_from = max(start, staff.joined_on) if staff.joined_on else start
        employed_to = min(end, staff.left_on - timedelta(days=1)) if staff.left_on else end
        employed = max(0, (employed_to - employed_from).days + 1)
        not_employed = Decimal(basis - employed)
    else:
        basis = len(full)
        not_employed = Decimal(len(full) - len(expected))
    leave = hr_selectors.approved_leave_by_day(staff, start, end)
    days = attendance_selectors.staff_days(staff, start, end)
    counts = {"worked": ZERO, "paid_leave": ZERO, "unpaid_leave": ZERO, "absent": ZERO}
    log_rows, warnings = [], []
    for day in expected:
        record = days.get(day)
        status = record.status if record is not None else None
        if day in leave:
            entry = leave[day]
            counts["paid_leave" if entry["paid"] else "unpaid_leave"] += entry["days"]
            counts["worked"] += 1 - entry["days"]
            log_rows.append({"date": str(day), "leave": entry["leave_type"], "days": str(entry["days"]),
                             "paid": entry["paid"]})
            continue
        if status in WORKED:
            counts["worked"] += 1
        elif status == "half_day":
            lost = HALF if settings.deduct_half_days else ZERO
            counts["worked"] += 1 - lost
            counts["absent"] += lost
        elif status == "absent":
            counts["absent"] += 1
        elif status == "leave":
            counts["paid_leave"] += 1
            warnings.append(f"{day}: marked leave in attendance with no approved leave request; paid.")
        elif settings.absence_basis == AbsenceBasis.UNRECORDED and day <= today:
            counts["absent"] += 1
            status = "unrecorded"
        else:
            counts["worked"] += 1
        if status not in WORKED:
            log_rows.append({"date": str(day), "attendance": status})

    overtime = 0
    if settings.overtime_enabled:
        day_minutes = attendance_selectors.scheduled_minutes(staff) or settings.default_day_minutes
        expected_set = set(expected)
        for day, record in days.items():
            if not record.worked_minutes or day in leave:
                continue
            extra = record.worked_minutes - (day_minutes if day in expected_set else 0)
            if extra >= settings.overtime_min_minutes:
                overtime += extra
    return {"basis": basis, "working": Decimal(len(expected)), "not_employed": not_employed, **counts,
            "overtime": overtime, "days": log_rows, "warnings": warnings}


def build_payslip(run, staff, salary, *, number, settings, overtime_override=None) -> Payslip:
    counts = _count_days(staff, run, settings)
    basic = salary.monthly_basic
    basis = counts["basis"] or 1
    unpaid = counts["unpaid_leave"] + counts["absent"] + counts["not_employed"]
    lines, warnings = [], list(counts["warnings"])
    if counts["basis"] == 0:
        warnings.append("No working days in the period by this person's schedule.")

    def add(kind, source, description, amount, component=None, taxable=False, pre_tax=False, adjustment=None):
        amount = q(amount)
        if amount > 0:
            lines.append(PayslipLine(kind=kind, source=source, description=description, amount=amount,
                                     component=component, is_taxable=taxable, is_pre_tax=pre_tax,
                                     adjustment=adjustment))
        return amount

    prorated = taxable_prorated = add(LineKind.EARNING, LineSource.BASIC, "Basic salary", basic, taxable=True)
    for component, value in component_values(salary):
        amount = component_amount(component, value, basic)
        if component.kind == ComponentKind.EARNING:
            add(LineKind.EARNING, LineSource.COMPONENT, component.name, amount, component=component,
                taxable=component.is_taxable)
            if component.prorate_for_absence:
                prorated += amount
                taxable_prorated += amount if component.is_taxable else ZERO
        else:
            add(LineKind.DEDUCTION, LineSource.COMPONENT, component.name, amount, component=component,
                pre_tax=component.is_pre_tax)

    unpaid_taxable = ZERO
    if unpaid > 0:
        share = min(unpaid / basis, Decimal(1))
        unpaid_amount = add(LineKind.DEDUCTION, LineSource.UNPAID_DAYS,
                            f"Unpaid days ({unpaid.quantize(Decimal('0.1'))} of {basis})", prorated * share)
        unpaid_taxable = q(taxable_prorated * share) if unpaid_amount else ZERO

    overtime_minutes = overtime_override if overtime_override is not None else counts["overtime"]
    if overtime_minutes and settings.overtime_enabled:
        day_minutes = attendance_selectors.scheduled_minutes(staff) or settings.default_day_minutes
        hourly = basic / basis / Decimal(day_minutes) * 60
        hours = Decimal(overtime_minutes) / 60
        add(LineKind.EARNING, LineSource.OVERTIME, f"Overtime ({q(hours)} h × {settings.overtime_multiplier})",
            hourly * hours * settings.overtime_multiplier, taxable=True)

    adjustments = list(PayrollAdjustment.objects.filter(staff=staff, payslip__isnull=True))
    for adj in adjustments:
        add(adj.kind, LineSource.ADJUSTMENT, adj.description, adj.amount, adjustment=adj,
            taxable=adj.kind == LineKind.EARNING and adj.is_taxable)

    earnings = sum((l.amount for l in lines if l.kind == LineKind.EARNING), ZERO)
    unpaid_line = sum((l.amount for l in lines if l.source == LineSource.UNPAID_DAYS), ZERO)
    gross = earnings - unpaid_line
    one_off = sum((l.amount for l in lines if l.is_taxable and l.source in ONE_OFF), ZERO)
    regular = sum((l.amount for l in lines if l.is_taxable and l.source not in ONE_OFF), ZERO) - unpaid_taxable
    pre_tax = sum((l.amount for l in lines if l.is_pre_tax), ZERO)
    scheme = scheme_for(staff, run.fiscal_year)
    if scheme is None:
        warnings.append("No tax scheme for this fiscal year and tax status; no tax withheld.")
    tax, taxable_income = monthly_tax(scheme, staff, regular=max(ZERO, regular), one_off=one_off, pre_tax=pre_tax)
    add(LineKind.DEDUCTION, LineSource.TAX, "Income tax (TDS)", tax)
    deductions = sum((l.amount for l in lines if l.kind == LineKind.DEDUCTION), ZERO) - unpaid_line
    net = gross - deductions
    if net < 0:
        warnings.append("Deductions exceed earnings: net pay is negative.")

    payslip = Payslip.objects.create(
        organization_id=run.organization_id, run=run, staff=staff, salary=salary, number=number, basic=basic,
        basis_days=counts["basis"], working_days=counts["working"], worked_days=counts["worked"],
        paid_leave_days=counts["paid_leave"], unpaid_leave_days=counts["unpaid_leave"],
        absent_days=counts["absent"], not_employed_days=counts["not_employed"],
        overtime_minutes=counts["overtime"], overtime_minutes_override=overtime_override,
        gross_pay=q(gross), taxable_income=taxable_income, tax=tax, total_deductions=q(deductions),
        net_pay=q(net), tax_scheme=scheme, details={"days": counts["days"], "warnings": warnings},
    )
    for line in lines:
        line.payslip = payslip
    PayslipLine.objects.bulk_create(lines)
    if adjustments:
        PayrollAdjustment.objects.filter(pk__in=[a.pk for a in adjustments]).update(payslip=payslip)
    return payslip


def recompute_payslip(payslip: Payslip, *, overtime_minutes=None, clear_override=False, by=None) -> Payslip:
    """Work one payslip out again (e.g. after setting its overtime by hand),
    keeping its number."""
    with transaction.atomic():
        run = _lock_run(payslip.run)
        _ensure_draft(run)
        override = None if clear_override else (
            overtime_minutes if overtime_minutes is not None else payslip.overtime_minutes_override)
        staff, salary, number = payslip.staff, payslip.salary, payslip.number
        before = payslip.overtime_minutes_override
        payslip.delete()
        payslip = build_payslip(run, staff, salary, number=number, settings=settings_for(run.organization_id),
                                overtime_override=override)
        log(AuditLog.Action.UPDATE, instance=payslip, module=MODULE, actor=by,
            changes={"overtime_minutes_override": {"before": before, "after": override}})
    return payslip


def approve_run(run: PayrollRun, *, by) -> PayrollRun:
    with transaction.atomic():
        run = _lock_run(run)
        _ensure_draft(run)
        if run.computed_at is None or not run.payslips.exists():
            raise ConflictError("Compute the run first; it has no payslips.", code="not_computed")
        run.status, run.approved_by, run.approved_at = RunStatus.APPROVED, by, timezone.now()
        run.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=run, module=MODULE, actor=by,
            changes={"status": {"before": RunStatus.DRAFT, "after": RunStatus.APPROVED}})
    users = [p.staff.user for p in run.payslips.select_related("staff__user") if p.staff.user_id]
    notify(users, event_type="PayslipIssued", organization_id=run.organization_id,
           title=f"Your payslip for {run.name} is ready", data={"payroll_run": run.pk})
    return run


def mark_paid(run: PayrollRun, *, reference: str = "", paid_at=None, by=None) -> PayrollRun:
    with transaction.atomic():
        run = _lock_run(run)
        if run.status != RunStatus.APPROVED:
            raise ConflictError("Only an approved run can be marked paid.", code="not_approved")
        run.status, run.paid_by, run.paid_at = RunStatus.PAID, by, paid_at or timezone.now()
        run.payment_reference = reference
        run.save(update_fields=["status", "paid_by", "paid_at", "payment_reference", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=run, module=MODULE, actor=by,
            changes={"status": {"before": RunStatus.APPROVED, "after": RunStatus.PAID}},
            metadata={"reference": reference})
    return run


def cancel_run(run: PayrollRun, *, reason: str, by=None) -> PayrollRun:
    """Only a draft: an approved run is corrected by adjustments in the next."""
    with transaction.atomic():
        run = _lock_run(run)
        _ensure_draft(run)
        run.payslips.all().delete()
        run.status, run.cancelled_reason = RunStatus.CANCELLED, reason
        run.save(update_fields=["status", "cancelled_reason", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=run, module=MODULE, actor=by,
            changes={"status": {"before": RunStatus.DRAFT, "after": RunStatus.CANCELLED}},
            metadata={"reason": reason})
    return run


# ---------------------------------------------------------------------------
# Adjustments
# ---------------------------------------------------------------------------
def adjustment_locked(adjustment: PayrollAdjustment) -> bool:
    return adjustment.payslip_id is not None and adjustment.payslip.run.status != RunStatus.DRAFT


def withdraw_adjustment(adjustment: PayrollAdjustment, *, by=None) -> None:
    """Take back an adjustment not yet on an approved payslip. If a draft
    payslip carries it, that payslip is worked out again without it."""
    with transaction.atomic():
        adjustment = PayrollAdjustment.objects.select_for_update().select_related("payslip__run").get(
            pk=adjustment.pk)
        if adjustment_locked(adjustment):
            raise ConflictError("This adjustment is on an approved payslip; add a correcting one instead.",
                                code="locked")
        payslip = adjustment.payslip
        log(AuditLog.Action.DELETE, instance=adjustment, module=MODULE, actor=by)
        if payslip is not None:
            PayslipLine.objects.filter(adjustment=adjustment).delete()
            adjustment.delete()
            recompute_payslip(payslip, by=by)
        else:
            adjustment.delete()
