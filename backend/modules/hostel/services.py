"""Allocating beds, check-in and check-out, room moves, complaints and the
term's hostel invoices.

Views and serializers call these so a rule lives in one place. Allocating
locks the bed row first (``select_for_update``), so two wardens can't hand
out the last bed twice; the partial unique constraints back that up.
"""
from datetime import date as Date, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.permissions.selectors import campus_ids_with_permission
from modules.finance.models import InvoiceSource
from modules.finance.services import ServiceCharge, generate_service_invoice, prorate
from modules.notifications.services import notify

from .models import (
    HOLDING,
    Allocation,
    AllocationStatus,
    Bed,
    BuildingGender,
    Complaint,
    ComplaintStatus,
)

MODULE = "hostel"
VIEW = "hostel.view"
MANAGE = "hostel.manage"
FINANCE_MANAGE = "finance.manage"


def holds(user, code: str, campus_id) -> bool:
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


def ensure_holds(user, code: str, campus_id) -> None:
    if not holds(user, code, campus_id):
        raise PermissionDeniedError("Your role does not cover this campus for this action.", code="wrong_campus")


# ---------------------------------------------------------------------------
# Allocations
# ---------------------------------------------------------------------------
def current_allocation(*, student=None, staff=None) -> Allocation | None:
    """The person's reserved or checked-in bed, if any."""
    qs = Allocation.objects.filter(status__in=HOLDING).select_related("bed__room__building", "bed__room__floor")
    if student is not None:
        return qs.filter(student=student).first()
    if staff is not None:
        return qs.filter(staff=staff).first()
    return None


def _check_occupant(bed: Bed, *, student, staff) -> None:
    if (student is None) == (staff is None):
        raise ServiceError("Name exactly one occupant: a student or a staff member.", code="bad_occupant")
    occupant = student or staff
    if occupant.organization_id != bed.organization_id:
        raise ServiceError("That person belongs to another organization.", code="wrong_organization")
    if student is not None and student.status != "active":
        raise ConflictError("Only an active student can be given a bed.", code="not_active")
    if staff is not None and staff.status == "left":
        raise ConflictError("This staff member has left.", code="not_active")
    building = bed.room.building
    if building.gender != BuildingGender.MIXED and occupant.gender != building.gender:
        raise ConflictError(f"{building.name} is for {building.get_gender_display().lower()}; "
                            f"this person's recorded gender doesn't match.", code="wrong_gender")


def allocate_hostel_room(*, bed: Bed, student=None, staff=None, start_date: Date | None = None, note: str = "",
                         by=None) -> Allocation:
    """Reserve ``bed`` for one person from ``start_date`` (default today).
    They check in separately, on or after that date."""
    start_date = start_date or timezone.localdate()
    with transaction.atomic():
        bed = Bed.objects.select_for_update().select_related("room__building").get(pk=bed.pk)
        room, building = bed.room, bed.room.building
        if by is not None:
            ensure_holds(by, MANAGE, building.campus_id)
        if not (bed.is_active and room.is_active and building.is_active):
            raise ConflictError("This bed is out of service.", code="bed_inactive")
        _check_occupant(bed, student=student, staff=staff)
        if Allocation.objects.filter(bed=bed, status__in=HOLDING).exists():
            raise ConflictError("This bed is already taken.", code="bed_taken")
        if current_allocation(student=student, staff=staff) is not None:
            raise ConflictError("This person already holds a bed; check them out or move them instead.",
                                code="already_allocated")
        allocation = Allocation.objects.create(
            organization_id=bed.organization_id, bed=bed, student=student, staff=staff, start_date=start_date,
            note=note, allocated_by=by)
        log(AuditLog.Action.CREATE, instance=allocation, module=MODULE, actor=by)
    return allocation


def _locked(allocation: Allocation) -> Allocation:
    return Allocation.objects.select_for_update().select_related("bed__room__building").get(pk=allocation.pk)


def check_in(allocation: Allocation, *, by=None) -> Allocation:
    with transaction.atomic():
        allocation = _locked(allocation)
        if allocation.status != AllocationStatus.RESERVED:
            raise ConflictError("Only a reserved bed can be checked into.", code="not_reserved")
        if allocation.start_date > timezone.localdate():
            raise ConflictError(f"This bed is reserved from {allocation.start_date}.", code="too_early")
        allocation.status = AllocationStatus.CHECKED_IN
        allocation.checked_in_at = timezone.now()
        allocation.save(update_fields=["status", "checked_in_at", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=allocation, module=MODULE, actor=by,
            changes={"status": {"before": AllocationStatus.RESERVED, "after": AllocationStatus.CHECKED_IN}})
    return allocation


def check_out(allocation: Allocation, *, on: Date | None = None, note: str = "", by=None) -> Allocation:
    """End a stay. ``on`` (default today) is the last day; it can't be in
    the future or before the stay began."""
    on = on or timezone.localdate()
    with transaction.atomic():
        allocation = _locked(allocation)
        if allocation.status != AllocationStatus.CHECKED_IN:
            raise ConflictError("Only someone checked in can check out.", code="not_checked_in")
        if on > timezone.localdate():
            raise ServiceError("Check-out can't be dated in the future.", code="future_date")
        if on < allocation.start_date:
            raise ServiceError("Check-out can't be before the stay began.", code="before_start")
        allocation.status = AllocationStatus.CHECKED_OUT
        allocation.end_date = on
        allocation.checked_out_at = timezone.now()
        allocation.end_note = note
        allocation.save(update_fields=["status", "end_date", "checked_out_at", "end_note", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=allocation, module=MODULE, actor=by,
            changes={"status": {"before": AllocationStatus.CHECKED_IN, "after": AllocationStatus.CHECKED_OUT},
                     "end_date": {"before": None, "after": on.isoformat()}})
    return allocation


def cancel_allocation(allocation: Allocation, reason: str, *, by=None) -> Allocation:
    if not reason.strip():
        raise ServiceError("Say why the reservation is cancelled.", code="reason_required")
    with transaction.atomic():
        allocation = _locked(allocation)
        if allocation.status != AllocationStatus.RESERVED:
            raise ConflictError("Only a reservation not yet checked into can be cancelled.", code="not_reserved")
        allocation.status = AllocationStatus.CANCELLED
        allocation.end_note = reason.strip()
        allocation.save(update_fields=["status", "end_note", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=allocation, module=MODULE, actor=by,
            changes={"status": {"before": AllocationStatus.RESERVED, "after": AllocationStatus.CANCELLED}})
    return allocation


def move(allocation: Allocation, *, bed: Bed, on: Date | None = None, note: str = "", by=None) -> Allocation:
    """Room change: the current stay ends the day before ``on`` and a new one,
    already checked in, starts on ``on`` (default today) — both or neither."""
    on = on or timezone.localdate()
    if bed.pk == allocation.bed_id:
        raise ServiceError("That's the bed they already have.", code="same_bed")
    with transaction.atomic():
        old = _locked(allocation)
        if old.status != AllocationStatus.CHECKED_IN:
            raise ConflictError("Only someone checked in can be moved.", code="not_checked_in")
        if on > timezone.localdate() or on <= old.start_date:
            raise ServiceError("Move them on a day after their stay began and not in the future.",
                               code="bad_date")
        old.status = AllocationStatus.CHECKED_OUT
        old.end_date = on - timedelta(days=1)
        old.checked_out_at = timezone.now()
        old.end_note = note or "Moved to another bed"
        old.save(update_fields=["status", "end_date", "checked_out_at", "end_note", "updated_at"])
        new = allocate_hostel_room(bed=bed, student=old.student, staff=old.staff, start_date=on, note=note, by=by)
        new.status = AllocationStatus.CHECKED_IN
        new.checked_in_at = timezone.now()
        new.save(update_fields=["status", "checked_in_at", "updated_at"])
    return new


# ---------------------------------------------------------------------------
# Complaints
# ---------------------------------------------------------------------------
def raise_complaint(*, building, room=None, category: str, title: str, description: str = "", by=None) -> Complaint:
    if room is not None and room.building_id != building.pk:
        raise ServiceError("That room isn't in this building.", code="wrong_building")
    complaint = Complaint.objects.create(
        organization_id=building.organization_id, building=building, room=room, category=category, title=title,
        description=description, raised_by=by)
    log(AuditLog.Action.CREATE, instance=complaint, module=MODULE, actor=by)
    if building.warden is not None and building.warden.user is not None:
        notify([building.warden.user], event_type="hostel.complaint", title=f"Hostel complaint: {title}",
               body=f"{building.name}{' room ' + room.number if room else ''}: {description[:200]}",
               data={"complaint": complaint.pk}, organization_id=building.organization_id)
    return complaint


def _set_complaint(complaint: Complaint, allowed, by, **fields) -> Complaint:
    with transaction.atomic():
        complaint = Complaint.objects.select_for_update().get(pk=complaint.pk)
        if complaint.status not in allowed:
            raise ConflictError(f"This complaint is already {complaint.get_status_display().lower()}.",
                                code="wrong_status")
        before = complaint.status
        for name, value in fields.items():
            setattr(complaint, name, value)
        complaint.save()
        log(AuditLog.Action.UPDATE, instance=complaint, module=MODULE, actor=by,
            changes={"status": {"before": before, "after": complaint.status}})
    if complaint.raised_by is not None and complaint.status != before:
        notify([complaint.raised_by], event_type="hostel.complaint_updated",
               title=f"Your complaint is {complaint.get_status_display().lower()}", body=complaint.title,
               data={"complaint": complaint.pk}, organization_id=complaint.organization_id)
    return complaint


def assign_complaint(complaint: Complaint, *, staff, by=None) -> Complaint:
    if staff.organization_id != complaint.organization_id:
        raise ServiceError("That staff member belongs to another organization.", code="wrong_organization")
    return _set_complaint(complaint, (ComplaintStatus.OPEN, ComplaintStatus.IN_PROGRESS), by,
                          assigned_to=staff, status=ComplaintStatus.IN_PROGRESS)


def resolve_complaint(complaint: Complaint, *, resolution: str, by=None) -> Complaint:
    if not resolution.strip():
        raise ServiceError("Say how it was resolved.", code="resolution_required")
    return _set_complaint(complaint, (ComplaintStatus.OPEN, ComplaintStatus.IN_PROGRESS), by,
                          status=ComplaintStatus.RESOLVED, resolution=resolution.strip(),
                          resolved_at=timezone.now(), resolved_by=by)


def reject_complaint(complaint: Complaint, *, reason: str, by=None) -> Complaint:
    if not reason.strip():
        raise ServiceError("Say why it is rejected.", code="reason_required")
    return _set_complaint(complaint, (ComplaintStatus.OPEN, ComplaintStatus.IN_PROGRESS), by,
                          status=ComplaintStatus.REJECTED, resolution=reason.strip(),
                          resolved_at=timezone.now(), resolved_by=by)


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------
def term_allocations(term, *, campus_ids=None, building=None):
    """Students' stays (not cancelled) that overlap ``term``."""
    qs = (Allocation.objects.filter(organization_id=term.organization_id, student__isnull=False,
                                    start_date__lte=term.end_date)
          .exclude(status=AllocationStatus.CANCELLED)
          .filter(Q(end_date__isnull=True) | Q(end_date__gte=term.start_date))
          .select_related("student", "bed__room__building", "bed__room__room_type__fee_category")
          .order_by("student_id", "start_date", "pk"))
    if campus_ids is not None:
        qs = qs.filter(bed__room__building__campus_id__in=campus_ids)
    if building is not None:
        qs = qs.filter(bed__room__building=building)
    return qs


def generate_term_invoices(term, *, by, building=None, due_date: Date | None = None) -> dict:
    """One hostel invoice per student who stays any part of ``term``: each
    stay is charged its room type's per-term fee, prorated by the days it
    covers. Students already billed for the term are skipped, so it is safe
    to rerun. Stays in room types without a fee category are skipped."""
    campus_ids = None
    if by is not None and not by.is_superuser:
        # Campuses where the caller both runs the hostel and bills fees.
        for code in (MANAGE, FINANCE_MANAGE):
            ids = campus_ids_with_permission(by, code)
            if ids is not None:
                campus_ids = set(ids) if campus_ids is None else campus_ids & set(ids)
    by_student = {}
    for stay in term_allocations(term, campus_ids=campus_ids, building=building):
        by_student.setdefault(stay.student, []).append(stay)

    created = skipped = not_enrolled = 0
    no_category = set()
    for student, stays in by_student.items():
        charges = []
        for stay in stays:
            room_type = stay.bed.room.room_type
            if room_type.fee_category is None:
                no_category.add(room_type.name)
                continue
            amount, first, last = prorate(room_type.fee_per_term, stay.start_date, stay.end_date or term.end_date,
                                          term.start_date, term.end_date)
            charges.append(ServiceCharge(
                category=room_type.fee_category, amount=amount,
                description=f"Hostel: {stay.bed.room} ({room_type.name}), {first:%Y-%m-%d} to {last:%Y-%m-%d}"))
        try:
            invoice = generate_service_invoice(student=student, term=term, source=InvoiceSource.HOSTEL,
                                               charges=charges, by=by, due_date=due_date)
        except ServiceError as exc:
            if exc.code != "not_enrolled":
                raise
            not_enrolled += 1
            continue
        if invoice is None:
            skipped += 1
        else:
            created += 1
    return {"created": created, "skipped": skipped, "not_enrolled": not_enrolled,
            "room_types_without_fee_category": sorted(no_category)}
