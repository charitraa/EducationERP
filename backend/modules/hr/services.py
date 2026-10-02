"""HR rules: contracts, leave balances and the leave workflow.

Views and serializers call these so a rule lives in one place. Attendance is
written only through ``modules.attendance.services``.
"""
from datetime import date as Date
from decimal import ROUND_FLOOR, Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.permissions.selectors import campus_ids_with_permission, users_holding
from modules.attendance import selectors as attendance_selectors
from modules.attendance import services as attendance_services
from modules.notifications.services import notify

from .models import (
    ZERO,
    Contract,
    FiscalYear,
    LeaveBalance,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
)

MODULE = "hr"
VIEW, MANAGE, APPROVE = "hr.view", "hr.manage", "hr.approve_leave"
HALF = Decimal("0.5")
ACTIVE = (LeaveStatus.PENDING, LeaveStatus.APPROVED)


def holds(user, code: str, campus_id) -> bool:
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


def ensure_campus_allowed(user, code: str, campus_id) -> None:
    if not holds(user, code, campus_id):
        raise PermissionDeniedError("Your role does not cover this campus for this action.", code="wrong_campus")


# ---------------------------------------------------------------------------
# Fiscal years and contracts
# ---------------------------------------------------------------------------
def fiscal_year_on(organization_id: int, day: Date) -> FiscalYear | None:
    return FiscalYear.objects.filter(organization_id=organization_id, start_date__lte=day,
                                     end_date__gte=day).first()


def overlapping_fiscal_years(organization_id: int, start: Date, end: Date, exclude=None):
    qs = FiscalYear.objects.filter(organization_id=organization_id, start_date__lte=end, end_date__gte=start)
    return qs.exclude(pk=exclude.pk) if exclude is not None else qs


def fiscal_year_in_use(year: FiscalYear) -> bool:
    return year.leave_balances.exists() or year.leave_requests.exists()


def overlapping_contracts(staff, start: Date, end: Date | None, exclude=None):
    qs = Contract.objects.filter(staff=staff).filter(Q(end_date__isnull=True) | Q(end_date__gte=start))
    if end is not None:
        qs = qs.filter(start_date__lte=end)
    return qs.exclude(pk=exclude.pk) if exclude is not None else qs


def current_contract(staff, on: Date | None = None) -> Contract | None:
    on = on or timezone.localdate()
    return (Contract.objects.filter(staff=staff, start_date__lte=on)
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=on)).select_related("position", "department")
            .first())


def end_contract(contract: Contract, *, end_date: Date, reason: str = "", by=None) -> Contract:
    if contract.end_date is not None and contract.end_date <= end_date:
        raise ConflictError("This contract already ends on or before that date.", code="already_ended")
    if end_date < contract.start_date:
        raise ServiceError("A contract can't end before it starts.", code="before_start")
    before = contract.end_date
    contract.end_date = end_date
    contract.ended_reason = reason
    contract.save(update_fields=["end_date", "ended_reason", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=contract, module=MODULE, actor=by,
        changes={"end_date": {"before": str(before) if before else None, "after": str(end_date)}},
        metadata={"reason": reason})
    return contract


# ---------------------------------------------------------------------------
# Leave balances
# ---------------------------------------------------------------------------
def _floor_half(value: Decimal) -> Decimal:
    """Round down to a half day."""
    return (value * 2).to_integral_value(rounding=ROUND_FLOOR) / 2


def entitlement(staff, leave_type: LeaveType, year: FiscalYear) -> Decimal | None:
    """The year's quota, or a share of it for someone who joined (or left)
    during the year."""
    if leave_type.annual_quota is None:
        return None
    quota = leave_type.annual_quota
    if not leave_type.prorate_for_joiners:
        return quota
    start = max(year.start_date, staff.joined_on) if staff.joined_on else year.start_date
    end = min(year.end_date, staff.left_on) if staff.left_on else year.end_date
    if start > end:
        return ZERO
    employed = (end - start).days + 1
    if employed >= year.days:
        return quota
    return _floor_half(quota * employed / year.days)


def _carry_forward(staff, leave_type: LeaveType, year: FiscalYear) -> Decimal:
    if leave_type.carry_forward_max <= 0 or leave_type.annual_quota is None:
        return ZERO
    previous = (LeaveBalance.objects.filter(staff=staff, leave_type=leave_type,
                                            fiscal_year__end_date__lt=year.start_date)
                .order_by("-fiscal_year__end_date").first())
    if previous is None or previous.total is None:
        return ZERO
    unused = previous.total - previous.used
    return max(ZERO, min(unused, leave_type.carry_forward_max))


def ensure_balance(staff, leave_type: LeaveType, year: FiscalYear) -> tuple[LeaveBalance, bool]:
    """The balance row, created on first use with the year's entitlement and
    whatever carries forward from the year before (fixed at that moment)."""
    balance = LeaveBalance.objects.filter(staff=staff, leave_type=leave_type, fiscal_year=year).first()
    if balance is not None:
        return balance, False
    with transaction.atomic():
        return LeaveBalance.objects.get_or_create(
            staff=staff, leave_type=leave_type, fiscal_year=year,
            defaults={"organization_id": staff.organization_id,
                      "entitled": entitlement(staff, leave_type, year),
                      "carried_forward": _carry_forward(staff, leave_type, year)},
        )


def pending_days(staff, leave_type, year, exclude=None) -> Decimal:
    qs = LeaveRequest.objects.filter(staff=staff, leave_type=leave_type, fiscal_year=year,
                                     status=LeaveStatus.PENDING)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs.aggregate(total=Sum("days"))["total"] or ZERO


def available(balance: LeaveBalance, exclude=None) -> Decimal | None:
    """Days that can still be asked for: the total, less what's taken and
    what's waiting for a decision. None when unlimited."""
    if balance.total is None:
        return None
    return balance.total - balance.used - pending_days(balance.staff, balance.leave_type, balance.fiscal_year,
                                                       exclude=exclude)


def adjust_balance(balance: LeaveBalance, *, delta: Decimal, reason: str, by=None) -> LeaveBalance:
    if balance.total is None:
        raise ServiceError("This leave type has no quota to adjust.", code="unlimited")
    with transaction.atomic():
        balance = LeaveBalance.objects.select_for_update().get(pk=balance.pk)
        before = balance.adjustment
        balance.adjustment += delta
        if balance.total < 0:
            raise ServiceError("That would leave a negative allowance.", code="negative_balance")
        balance.save(update_fields=["adjustment", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=balance, module=MODULE, actor=by,
            changes={"adjustment": {"before": str(before), "after": str(balance.adjustment)}},
            metadata={"reason": reason})
    return balance


def open_balances(year: FiscalYear, *, staff_members) -> dict:
    """Balances for every quota-bearing active leave type, for each of
    ``staff_members`` employed during the year. Safe to run again."""
    types = [t for t in LeaveType.objects.filter(organization_id=year.organization_id, is_active=True)]
    created = existing = 0
    for staff in staff_members:
        if staff.joined_on and staff.joined_on > year.end_date:
            continue
        if staff.left_on and staff.left_on < year.start_date:
            continue
        for leave_type in types:
            if leave_type.gender and staff.gender != leave_type.gender:
                continue
            _, was_created = ensure_balance(staff, leave_type, year)
            created += was_created
            existing += not was_created
    return {"created": created, "existing": existing}


# ---------------------------------------------------------------------------
# Leave requests
# ---------------------------------------------------------------------------
def leave_days(staff, start: Date, end: Date) -> list[Date]:
    return attendance_selectors.staff_working_days(staff, start, end)


def _employed_throughout(staff, start: Date, end: Date) -> bool:
    if staff.joined_on and start < staff.joined_on:
        return False
    # ``left_on`` is the first day no longer employed (see ``works_on``).
    return not (staff.left_on and end >= staff.left_on)


def apply_leave(*, staff, leave_type: LeaveType, start_date: Date, end_date: Date, half_day: bool = False,
                reason: str = "", by=None, notify_approvers: bool = True) -> LeaveRequest:
    """``notify_approvers=False`` when the request is approved in the same
    breath (the applications module), so approvers aren't asked to act."""
    if not leave_type.is_active:
        raise ServiceError("This leave type is no longer in use.", code="inactive_leave_type")
    if end_date < start_date:
        raise ServiceError("The leave can't end before it starts.", code="dates_reversed")
    if half_day and (start_date != end_date or not leave_type.allow_half_day):
        raise ServiceError("A half day is one day, of a type that allows half days.", code="half_day_not_allowed")
    if leave_type.gender and staff.gender != leave_type.gender:
        raise ServiceError("This leave type isn't available to this staff member.", code="not_eligible")
    if not _employed_throughout(staff, start_date, end_date):
        raise ServiceError("The staff member isn't employed on every one of those days.", code="not_employed")
    year = fiscal_year_on(staff.organization_id, start_date)
    if year is None:
        raise ServiceError("No fiscal year covers that date; HR must add one first.", code="no_fiscal_year")
    if end_date > year.end_date:
        raise ServiceError(f"The leave crosses into the next fiscal year after {year.end_date}; "
                           "apply for each year separately.", code="spans_fiscal_years")
    working = leave_days(staff, start_date, end_date)
    if not working:
        raise ServiceError("Those are all days off already (weekends or holidays).", code="no_working_days")
    days = HALF if half_day else Decimal(len(working))

    with transaction.atomic():
        balance, _ = ensure_balance(staff, leave_type, year)
        balance = LeaveBalance.objects.select_for_update().get(pk=balance.pk)
        clash = LeaveRequest.objects.filter(staff=staff, status__in=ACTIVE, start_date__lte=end_date,
                                            end_date__gte=start_date).first()
        if clash is not None:
            raise ConflictError(f"This overlaps leave already {clash.get_status_display().lower()} "
                                f"({clash.start_date}–{clash.end_date}).", code="overlaps")
        left = available(balance)
        if left is not None and days > left:
            raise ServiceError(f"Only {left} day(s) of {leave_type.name} are left this year.",
                               code="insufficient_balance")
        request = LeaveRequest.objects.create(
            organization_id=staff.organization_id, staff=staff, leave_type=leave_type, fiscal_year=year,
            start_date=start_date, end_date=end_date, half_day=half_day, days=days, reason=reason, applied_by=by,
        )
        log(AuditLog.Action.CREATE, instance=request, module=MODULE, actor=by)
    if notify_approvers:
        _notify_approvers(request)
    return request


def _notify_approvers(request: LeaveRequest) -> None:
    staff = request.staff
    recipients = [u for u in users_holding([APPROVE], campus_id=staff.campus_id,
                                           organization_id=staff.organization_id) if u.pk != staff.user_id]
    notify(recipients, event_type="LeaveRequested", organization_id=staff.organization_id,
           title=f"Leave request from {staff.full_name}",
           body=f"{request.leave_type.name}: {request.start_date} to {request.end_date} ({request.days} day(s)).",
           data={"leave_request": request.pk})


def _lock(request: LeaveRequest) -> LeaveRequest:
    return LeaveRequest.objects.select_for_update().select_related("staff", "leave_type", "fiscal_year").get(
        pk=request.pk)


def _tag(request: LeaveRequest) -> str:
    return f"Leave request #{request.pk}: {request.leave_type.name}"


def ensure_can_decide(user, request: LeaveRequest) -> None:
    if not holds(user, APPROVE, request.staff.campus_id):
        raise PermissionDeniedError("Your role does not cover this campus for this action.", code="wrong_campus")
    if request.staff.user_id is not None and request.staff.user_id == user.pk:
        raise PermissionDeniedError("You can't decide your own leave request.", code="own_request")


def approve_leave(request: LeaveRequest, *, by, note: str = "") -> LeaveRequest:
    ensure_can_decide(by, request)
    with transaction.atomic():
        request = _lock(request)
        if request.status != LeaveStatus.PENDING:
            raise ConflictError(f"This request is already {request.get_status_display().lower()}.",
                                code="not_pending")
        balance, _ = ensure_balance(request.staff, request.leave_type, request.fiscal_year)
        balance = LeaveBalance.objects.select_for_update().get(pk=balance.pk)
        left = available(balance, exclude=request)
        if left is not None and request.days > left:
            raise ServiceError(f"Only {left} day(s) of {request.leave_type.name} are left; "
                               "adjust the balance or reject.", code="insufficient_balance")
        balance.used += request.days
        balance.save(update_fields=["used", "updated_at"])
        request.status = LeaveStatus.APPROVED
        request.decided_by, request.decided_at, request.decision_note = by, timezone.now(), note
        request.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=request, module=MODULE, actor=by,
            changes={"status": {"before": LeaveStatus.PENDING, "after": LeaveStatus.APPROVED}})
        if not request.half_day:
            # A half day leaves the day to the punches: the other half is worked.
            for day in leave_days(request.staff, request.start_date, request.end_date):
                attendance_services.set_staff_day(staff=request.staff, day=day, status="leave", note=_tag(request),
                                                  by=by)
    _notify_applicant(request, "approved")
    return request


def reject_leave(request: LeaveRequest, *, by, note: str) -> LeaveRequest:
    ensure_can_decide(by, request)
    with transaction.atomic():
        request = _lock(request)
        if request.status != LeaveStatus.PENDING:
            raise ConflictError(f"This request is already {request.get_status_display().lower()}.",
                                code="not_pending")
        request.status = LeaveStatus.REJECTED
        request.decided_by, request.decided_at, request.decision_note = by, timezone.now(), note
        request.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=request, module=MODULE, actor=by,
            changes={"status": {"before": LeaveStatus.PENDING, "after": LeaveStatus.REJECTED}})
    _notify_applicant(request, "rejected")
    return request


def can_cancel(user, request: LeaveRequest) -> bool:
    """The applicant, while it's pending or hasn't started; HR or an approver
    for the campus, any time."""
    if holds(user, APPROVE, request.staff.campus_id) or holds(user, MANAGE, request.staff.campus_id):
        return True
    is_own = request.staff.user_id is not None and request.staff.user_id == user.pk
    if not is_own:
        return False
    return request.status == LeaveStatus.PENDING or request.start_date > timezone.localdate()


def cancel_leave(request: LeaveRequest, *, by) -> LeaveRequest:
    if not can_cancel(by, request):
        raise PermissionDeniedError("Leave that has started can only be cancelled by HR or an approver.",
                                    code="cannot_cancel")
    with transaction.atomic():
        request = _lock(request)
        if request.status not in ACTIVE:
            raise ConflictError(f"This request is already {request.get_status_display().lower()}.",
                                code="not_active")
        was = request.status
        if was == LeaveStatus.APPROVED:
            balance = LeaveBalance.objects.select_for_update().get(
                staff=request.staff, leave_type=request.leave_type, fiscal_year=request.fiscal_year)
            balance.used = max(ZERO, balance.used - request.days)
            balance.save(update_fields=["used", "updated_at"])
            tag = _tag(request)
            days = attendance_selectors.staff_days(request.staff, request.start_date, request.end_date)
            for staff_day in days.values():
                if staff_day.is_override and staff_day.status == "leave" and staff_day.note == tag:
                    attendance_services.clear_staff_day_override(staff_day=staff_day, by=by)
        request.status = LeaveStatus.CANCELLED
        request.cancelled_by, request.cancelled_at = by, timezone.now()
        request.save(update_fields=["status", "cancelled_by", "cancelled_at", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=request, module=MODULE, actor=by,
            changes={"status": {"before": was, "after": LeaveStatus.CANCELLED}})
    if request.staff.user_id != getattr(by, "pk", None):
        _notify_applicant(request, "cancelled")
    return request


def _notify_applicant(request: LeaveRequest, outcome: str) -> None:
    user = request.staff.user
    if user is None:
        return
    body = f"{request.leave_type.name}: {request.start_date} to {request.end_date}."
    if request.decision_note:
        body += f" Note: {request.decision_note}"
    notify([user], event_type=f"Leave{outcome.capitalize()}", organization_id=request.organization_id,
           title=f"Your leave request was {outcome}", body=body, data={"leave_request": request.pk})
