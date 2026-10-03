"""Putting riders on routes, running trips, and the term's transport invoices.

Views and serializers call these so a rule lives in one place. Assigning a
rider locks the route row first (``select_for_update``), so two clerks can't
both fill the last seat.

A trip's roll is taken by the route's own crew (its driver or assistant) or
by anyone holding ``transport.manage`` for the campus — the way a teacher
marks their own class in the attendance module without needing an office permission.
"""
from datetime import date as Date

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
from modules.parents.selectors import links_for_student

from .models import (
    Assignment,
    BoardingStatus,
    CrewRole,
    Direction,
    Route,
    Trip,
    TripRecord,
    TripStatus,
)

MODULE = "transport"
VIEW = "transport.view"
MANAGE = "transport.manage"
FINANCE_MANAGE = "finance.manage"


def holds(user, code: str, campus_id) -> bool:
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


def ensure_holds(user, code: str, campus_id) -> None:
    if not holds(user, code, campus_id):
        raise PermissionDeniedError("Your role does not cover this campus for this action.", code="wrong_campus")


def is_crew(user, route_or_trip) -> bool:
    """The signed-in user drives or assists this route (or that day's trip)."""
    for crew in (route_or_trip.driver, route_or_trip.assistant):
        if crew is not None and crew.staff.user_id is not None and crew.staff.user_id == user.pk:
            return True
    return False


def ensure_can_run(user, route_or_trip) -> None:
    campus_id = (route_or_trip.route if isinstance(route_or_trip, Trip) else route_or_trip).campus_id
    if is_crew(user, route_or_trip) or holds(user, MANAGE, campus_id):
        return
    raise PermissionDeniedError("Only this route's crew or the transport office can do this.", code="not_crew")


def check_crew(route: Route) -> None:
    """Crew roles: a driver must be a driver; anyone crewing must be active."""
    if route.driver is not None and route.driver.role != CrewRole.DRIVER:
        raise ServiceError("That person is an assistant, not a driver.", code="not_a_driver")
    for crew in (route.driver, route.assistant):
        if crew is not None and not crew.is_active:
            raise ServiceError(f"{crew} is no longer active crew.", code="inactive_crew")
    if route.driver is not None and route.assistant is not None and route.driver.pk == route.assistant.pk:
        raise ServiceError("The driver can't also be the assistant.", code="same_crew")


# ---------------------------------------------------------------------------
# Riders
# ---------------------------------------------------------------------------
def active_on(day: Date):
    """Assignments in effect on ``day``."""
    return Assignment.objects.filter(start_date__lte=day).filter(Q(end_date__isnull=True) | Q(end_date__gte=day))


def current_assignment(*, student=None, staff=None, on: Date | None = None) -> Assignment | None:
    qs = active_on(on or timezone.localdate()).select_related("route", "stop")
    if student is not None:
        return qs.filter(student=student).first()
    if staff is not None:
        return qs.filter(staff=staff).first()
    return None


def assign_rider(*, route: Route, stop, student=None, staff=None, direction: str = Direction.BOTH,
                 start_date: Date | None = None, by=None) -> Assignment:
    """Put one person on ``route`` at ``stop`` from ``start_date`` (default
    today). Refused once the route's vehicle is full that day."""
    start_date = start_date or timezone.localdate()
    if (student is None) == (staff is None):
        raise ServiceError("Name exactly one rider: a student or a staff member.", code="bad_rider")
    rider = student or staff
    with transaction.atomic():
        route = Route.objects.select_for_update().select_related("vehicle").get(pk=route.pk)
        if by is not None:
            ensure_holds(by, MANAGE, route.campus_id)
        if rider.organization_id != route.organization_id or stop.organization_id != route.organization_id:
            raise ServiceError("That record belongs to another organization.", code="wrong_organization")
        if stop.route_id != route.pk:
            raise ServiceError("That stop isn't on this route.", code="wrong_stop")
        if not route.is_active:
            raise ConflictError("This route isn't running.", code="route_inactive")
        if student is not None and student.status != "active":
            raise ConflictError("Only an active student can be put on a route.", code="not_active")
        if staff is not None and staff.status == "left":
            raise ConflictError("This staff member has left.", code="not_active")
        mine = Assignment.objects.filter(student=student) if student is not None else Assignment.objects.filter(
            staff=staff)
        if mine.filter(Q(end_date__isnull=True) | Q(end_date__gte=start_date)).exists():
            raise ConflictError("This person already rides a route then; end that first.",
                                code="already_assigned")
        if route.vehicle is not None:
            riding = active_on(start_date).filter(route=route).count()
            if riding >= route.vehicle.capacity:
                raise ConflictError(f"{route.vehicle.name} is full ({route.vehicle.capacity} seats).",
                                    code="route_full", details={"capacity": route.vehicle.capacity})
        assignment = Assignment.objects.create(
            organization_id=route.organization_id, route=route, stop=stop, student=student, staff=staff,
            direction=direction, start_date=start_date, assigned_by=by)
        log(AuditLog.Action.CREATE, instance=assignment, module=MODULE, actor=by)
    return assignment


def end_assignment(assignment: Assignment, *, on: Date | None = None, reason: str = "", by=None) -> Assignment:
    """Stop riding after ``on`` (the last day, default today)."""
    on = on or timezone.localdate()
    with transaction.atomic():
        assignment = Assignment.objects.select_for_update().get(pk=assignment.pk)
        if assignment.end_date is not None and assignment.end_date <= on:
            raise ConflictError("This assignment has already ended.", code="already_ended")
        if on < assignment.start_date:
            raise ServiceError("It can't end before it starts.", code="before_start")
        before = assignment.end_date
        assignment.end_date = on
        assignment.end_reason = reason
        assignment.save(update_fields=["end_date", "end_reason", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=assignment, module=MODULE, actor=by,
            changes={"end_date": {"before": before and before.isoformat(), "after": on.isoformat()}})
    return assignment


# ---------------------------------------------------------------------------
# Trips
# ---------------------------------------------------------------------------
def riders_for(trip: Trip):
    """Who should be on this trip: the route's riders that day, in stop order,
    leaving out those riding only the other way."""
    other_way = Direction.DROP if trip.direction == "pickup" else Direction.PICKUP
    return (active_on(trip.date).filter(route_id=trip.route_id).exclude(direction=other_way)
            .select_related("stop", "student", "staff").order_by("stop__sequence", "pk"))


def open_trip(*, route: Route, date: Date, direction: str, by) -> tuple[Trip, bool]:
    """Today's (or a past day's) run, created on first use and returned again
    after that. Returns ``(trip, created)``."""
    if date > timezone.localdate():
        raise ServiceError("A trip can't be opened for a future day.", code="future_date")
    ensure_can_run(by, route)
    with transaction.atomic():
        Route.objects.select_for_update().get(pk=route.pk)
        trip = Trip.objects.filter(route=route, date=date, direction=direction).first()
        if trip is not None:
            return trip, False
        if not route.is_active:
            raise ConflictError("This route isn't running.", code="route_inactive")
        trip = Trip.objects.create(
            organization_id=route.organization_id, route=route, date=date, direction=direction,
            vehicle=route.vehicle, driver=route.driver, assistant=route.assistant, opened_by=by)
        log(AuditLog.Action.CREATE, instance=trip, module=MODULE, actor=by)
    return trip, True


def mark_trip(trip: Trip, entries: list[dict], *, by) -> list[TripRecord]:
    """Record who boarded. ``entries`` are ``{assignment, status, at?, note?}``;
    marking someone again replaces their earlier mark. A completed trip can
    still be corrected, but only by the transport office."""
    ensure_can_run(by, trip)
    if trip.status == TripStatus.COMPLETED:
        ensure_holds(by, MANAGE, trip.route.campus_id)
    expected = {a.pk: a for a in riders_for(trip)}
    if len({e["assignment"].pk for e in entries}) != len(entries):
        raise ServiceError("Each rider can appear once.", code="duplicate_rider")
    newly_absent = []
    rows = []
    with transaction.atomic():
        Trip.objects.select_for_update().get(pk=trip.pk)
        for entry in entries:
            assignment = expected.get(entry["assignment"].pk)
            if assignment is None:
                raise ServiceError(f"{entry['assignment'].rider_name} doesn't ride this trip.", code="not_a_rider",
                                   details={"assignment": entry["assignment"].pk})
            record = TripRecord.objects.filter(trip=trip, assignment=assignment).first()
            was = record.status if record is not None else None
            if record is None:
                record = TripRecord(organization_id=trip.organization_id, trip=trip, assignment=assignment)
            record.status = entry["status"]
            record.at = entry.get("at")
            record.note = entry.get("note", "")
            record.marked_by = by
            record.save()
            rows.append(record)
            if record.status == BoardingStatus.ABSENT and was != BoardingStatus.ABSENT and assignment.student_id:
                newly_absent.append(assignment)
        log(AuditLog.Action.UPDATE, instance=trip, module=MODULE, actor=by,
            changes={"marked": {"before": None, "after": len(rows)}})
    for assignment in newly_absent:
        _tell_parents_absent(trip, assignment)
    return rows


def _tell_parents_absent(trip: Trip, assignment: Assignment) -> None:
    student = assignment.student
    parents = [link.parent.user for link in links_for_student(student)]
    run = "morning pickup" if trip.direction == "pickup" else "afternoon drop"
    notify(parents, event_type="transport.absent", title=f"{student.full_name} wasn't on the bus",
           body=f"{trip.route.name}, {run} on {trip.date:%Y-%m-%d} at {assignment.stop.name}.",
           data={"trip": trip.pk, "student": student.pk}, organization_id=trip.organization_id)


def complete_trip(trip: Trip, *, by) -> Trip:
    ensure_can_run(by, trip)
    with transaction.atomic():
        trip = Trip.objects.select_for_update().select_related("route").get(pk=trip.pk)
        if trip.status == TripStatus.COMPLETED:
            raise ConflictError("This trip is already completed.", code="already_completed")
        trip.status = TripStatus.COMPLETED
        trip.completed_at = timezone.now()
        trip.save(update_fields=["status", "completed_at", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=trip, module=MODULE, actor=by,
            changes={"status": {"before": TripStatus.OPEN, "after": TripStatus.COMPLETED}})
    return trip


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------
def term_assignments(term, *, campus_ids=None, route=None):
    """Students' assignments that overlap ``term``."""
    qs = (Assignment.objects.filter(organization_id=term.organization_id, student__isnull=False,
                                    start_date__lte=term.end_date)
          .filter(Q(end_date__isnull=True) | Q(end_date__gte=term.start_date))
          .select_related("student", "route__fee_category", "stop")
          .order_by("student_id", "start_date", "pk"))
    if campus_ids is not None:
        qs = qs.filter(route__campus_id__in=campus_ids)
    if route is not None:
        qs = qs.filter(route=route)
    return qs


def generate_term_invoices(term, *, by, route=None, due_date: Date | None = None) -> dict:
    """One transport invoice per student riding any part of ``term``: each
    assignment is charged its stop's fee (or else the route's), prorated by
    the days it covers. Students already billed for the term are skipped, so
    it is safe to rerun. Routes without a fee category are skipped."""
    campus_ids = None
    if by is not None and not by.is_superuser:
        # Campuses where the caller both runs transport and bills fees.
        for code in (MANAGE, FINANCE_MANAGE):
            ids = campus_ids_with_permission(by, code)
            if ids is not None:
                campus_ids = set(ids) if campus_ids is None else campus_ids & set(ids)
    by_student = {}
    for assignment in term_assignments(term, campus_ids=campus_ids, route=route):
        by_student.setdefault(assignment.student, []).append(assignment)

    created = skipped = not_enrolled = 0
    no_category = set()
    for student, assignments in by_student.items():
        charges = []
        for a in assignments:
            if a.route.fee_category is None:
                no_category.add(a.route.name)
                continue
            amount, first, last = prorate(a.stop.fee, a.start_date, a.end_date or term.end_date,
                                          term.start_date, term.end_date)
            charges.append(ServiceCharge(
                category=a.route.fee_category, amount=amount,
                description=f"Transport: {a.route.name}, {a.stop.name}, {first:%Y-%m-%d} to {last:%Y-%m-%d}"))
        try:
            invoice = generate_service_invoice(student=student, term=term, source=InvoiceSource.TRANSPORT,
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
            "routes_without_fee_category": sorted(no_category)}
