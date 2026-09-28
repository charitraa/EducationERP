"""Event rules: registering, checking in, recording participation, points
and awards.

Views and serializers call these so a rule lives in one place.
"""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.permissions.selectors import campus_ids_with_permission
from modules.staff.selectors import staff_member_for_user

from .models import (
    AttendanceStatus,
    AwardRule,
    Event,
    EventAttendance,
    EventParticipation,
    EventRegistration,
    EventStatus,
    ParticipationRole,
    PointEntry,
    PointRule,
    PointSource,
    RegistrationMode,
    RegistrationStatus,
    StudentAward,
    StudentPoints,
    ThresholdKind,
)

MODULE = "events"
VIEW, COORDINATE, MANAGE = "events.view", "events.coordinate", "events.manage"


def holds(user, code: str, campus_id) -> bool:
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


def coordinates(user, event: Event) -> bool:
    staff = staff_member_for_user(user)
    return staff is not None and event.organized_by_id == staff.pk


def can_run(user, event: Event) -> bool:
    """The office (org-wide or at this campus), or the event's own organizer."""
    if holds(user, MANAGE, event.campus_id):
        return True
    return holds(user, COORDINATE, event.campus_id) and coordinates(user, event)


def ensure_can_run(user, event: Event) -> None:
    if not can_run(user, event):
        raise PermissionDeniedError("Only this event's organizer, or the office, can do this.",
                                    code="not_your_event")


# ---------------------------------------------------------------------------
# Event lifecycle
# ---------------------------------------------------------------------------
def publish_event(event: Event, *, by=None) -> Event:
    if event.status != EventStatus.DRAFT:
        raise ConflictError("Only an event being set up can be published.", code="not_draft")
    event.status = EventStatus.PUBLISHED
    event.save(update_fields=["status", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=event, module=MODULE, actor=by,
        changes={"status": {"before": "draft", "after": "published"}})
    return event


def cancel_event(event: Event, reason: str, *, by=None) -> Event:
    if not reason.strip():
        raise ServiceError("Say why the event is being cancelled.", code="reason_required")
    if event.status == EventStatus.CANCELLED:
        raise ConflictError("Already cancelled.", code="already_cancelled")
    event.status = EventStatus.CANCELLED
    event.cancelled_at, event.cancelled_reason = timezone.now(), reason.strip()
    event.save(update_fields=["status", "cancelled_at", "cancelled_reason", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=event, module=MODULE, actor=by,
        changes={"status": {"before": event.status, "after": "cancelled"}}, metadata={"reason": reason.strip()})
    return event


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def _confirmed_count(event: Event) -> int:
    return event.registrations.filter(status=RegistrationStatus.CONFIRMED).count()


def _ensure_capacity(event: Event) -> None:
    if event.capacity is not None and _confirmed_count(event) >= event.capacity:
        raise ConflictError("This event is full.", code="event_full")


def register(event: Event, student, *, note: str = "", by=None) -> EventRegistration:
    if event.status != EventStatus.PUBLISHED:
        raise ConflictError("This event isn't open for registration.", code="not_published")
    if not event.registration_open:
        raise ConflictError("Registration is closed.", code="registration_closed")
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event.pk)
        existing = EventRegistration.objects.exclude(status=RegistrationStatus.WITHDRAWN).filter(
            event=event, student=student).first()
        if existing is not None:
            raise ConflictError("Already registered for this event.", code="already_registered")
        status = RegistrationStatus.PENDING
        if event.registration_mode == RegistrationMode.OPEN:
            _ensure_capacity(event)
            status = RegistrationStatus.CONFIRMED
        registration = EventRegistration.objects.create(
            organization_id=event.organization_id, event=event, student=student, note=note, status=status,
        )
    if registration.status == RegistrationStatus.CONFIRMED:
        _notify_registration_confirmed(registration)
    return registration


def decide_registration(registration: EventRegistration, approve: bool, *, note: str = "", by=None) -> EventRegistration:
    with transaction.atomic():
        registration = EventRegistration.objects.select_for_update().select_related("event").get(pk=registration.pk)
        event = registration.event
        if registration.status != RegistrationStatus.PENDING:
            raise ConflictError("This registration has already been decided.", code="already_decided")
        if approve:
            Event.objects.select_for_update().get(pk=event.pk)
            _ensure_capacity(event)
            registration.status = RegistrationStatus.CONFIRMED
        else:
            if not note.strip():
                raise ServiceError("Say why it's being rejected.", code="reason_required")
            registration.status = RegistrationStatus.REJECTED
        registration.decided_by, registration.decided_at, registration.decision_note = by, timezone.now(), note.strip()
        registration.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=registration, module=MODULE, actor=by,
            changes={"status": {"before": "pending", "after": registration.status}})
    if registration.status == RegistrationStatus.CONFIRMED:
        _notify_registration_confirmed(registration)
    return registration


def _notify_registration_confirmed(registration: EventRegistration) -> None:
    """``EventRegistered`` (claude.md section 26): tell the student and
    their guardians their registration was confirmed."""
    from modules.notifications.services import notify
    from modules.parents.selectors import links_for_student

    student = registration.student
    recipients = [student.user] if student.user_id else []
    recipients += [link.parent.user for link in links_for_student(student) if link.parent.user_id]
    notify(recipients, event_type="events.registration_confirmed",
          title=f"Registration confirmed: {registration.event.name}",
          body=f"{student.full_name}'s registration for {registration.event.name} is confirmed.",
          data={"registration": registration.pk}, organization_id=registration.organization_id)


def withdraw_registration(registration: EventRegistration, *, by=None) -> EventRegistration:
    if registration.status == RegistrationStatus.WITHDRAWN:
        raise ConflictError("Already withdrawn.", code="already_withdrawn")
    registration.status = RegistrationStatus.WITHDRAWN
    registration.decided_by, registration.decided_at = by, timezone.now()
    registration.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=registration, module=MODULE, actor=by,
        changes={"status": {"before": "confirmed", "after": "withdrawn"}})
    return registration


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------
def award_points(student, points: int, reason: str, *, rule=None, event=None, by=None) -> PointEntry:
    if points == 0:
        raise ServiceError("Give a non-zero number of points.", code="bad_amount")
    with transaction.atomic():
        entry = PointEntry.objects.create(
            organization_id=student.organization_id, student=student, points=points, reason=reason, rule=rule,
            event=event, awarded_by=by,
        )
        totals, _ = StudentPoints.objects.select_for_update().get_or_create(
            student=student, defaults={"organization_id": student.organization_id, "total": 0})
        totals.total = totals.total + points
        totals.save(update_fields=["total", "updated_at"])
        log(AuditLog.Action.CREATE, instance=entry, module=MODULE, actor=by,
            metadata={"points": points, "reason": reason, "total": totals.total})
        evaluate_awards(student, by=by)
    return entry


def _matching_rules(event: Event, source: str, role: str | None = None):
    """Active point rules that apply to this event: any category-wide rule,
    plus the event's own category."""
    rules = PointRule.objects.filter(
        organization_id=event.organization_id, is_active=True, source=source,
    ).filter(Q(category__isnull=True) | Q(category_id=event.category_id))
    if role is not None:
        rules = rules.filter(role=role)
    return rules


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------
def mark_attendance(event: Event, entries: list[tuple[int, str]], *, by=None) -> list[EventAttendance]:
    """``entries`` is (student_id, status) pairs. Points for attending are
    only awarded the moment a student first becomes present — re-marking
    present, or marking absent, awards nothing further."""
    from modules.students.models import Student

    if not entries:
        raise ServiceError("Give at least one student.", code="no_entries")
    saved = []
    with transaction.atomic():
        for student_id, status in entries:
            record = EventAttendance.objects.select_for_update().filter(event=event, student_id=student_id).first()
            was_present = record is not None and record.status == AttendanceStatus.PRESENT
            checked_in_at = timezone.now() if status == AttendanceStatus.PRESENT else None
            if record is None:
                record = EventAttendance.objects.create(
                    organization_id=event.organization_id, event=event, student_id=student_id, status=status,
                    marked_by=by, checked_in_at=checked_in_at)
            else:
                record.status, record.marked_by, record.checked_in_at = status, by, checked_in_at
                record.save(update_fields=["status", "marked_by", "checked_in_at", "updated_at"])
            saved.append(record)
            if status == AttendanceStatus.PRESENT and not was_present:
                student = Student.objects.get(pk=student_id)
                for rule in _matching_rules(event, PointSource.ATTENDANCE):
                    award_points(student, rule.points, f"Attended {event.name}", rule=rule, event=event, by=by)
                # A rule might key off attendance counts alone, with no points involved.
                evaluate_awards(student, by=by)
    return saved


# ---------------------------------------------------------------------------
# Participation
# ---------------------------------------------------------------------------
def record_participation(event: Event, student, role: str, *, position=None, remark: str = "",
                         by=None) -> EventParticipation:
    """Points for a role are awarded once, the first time it's recorded;
    changing the position or remark afterwards doesn't award it again."""
    with transaction.atomic():
        participation = EventParticipation.objects.select_for_update().filter(
            event=event, student=student, role=role).first()
        is_new = participation is None
        if is_new:
            participation = EventParticipation.objects.create(
                organization_id=event.organization_id, event=event, student=student, role=role, position=position,
                remark=remark, recorded_by=by)
        else:
            participation.position, participation.remark, participation.recorded_by = position, remark, by
            participation.save(update_fields=["position", "remark", "recorded_by", "updated_at"])
        if is_new:
            for rule in _matching_rules(event, PointSource.PARTICIPATION, role=role):
                award_points(student, rule.points, f"{rule.get_role_display()} at {event.name}", rule=rule,
                            event=event, by=by)
            # A rule might key off participation counts alone, with no points involved.
            evaluate_awards(student, by=by)
    return participation


# ---------------------------------------------------------------------------
# Awards
# ---------------------------------------------------------------------------
def _count_for(student, threshold_kind: str, category) -> int:
    if threshold_kind == ThresholdKind.POINTS_TOTAL:
        totals = StudentPoints.objects.filter(student=student).first()
        return totals.total if totals else 0
    if threshold_kind == ThresholdKind.EVENTS_ATTENDED:
        qs = EventAttendance.objects.filter(student=student, status=AttendanceStatus.PRESENT)
        if category is not None:
            qs = qs.filter(event__category=category)
        return qs.count()
    if threshold_kind == ThresholdKind.EVENTS_WON:
        qs = EventParticipation.objects.filter(student=student, role=ParticipationRole.WINNER)
        if category is not None:
            qs = qs.filter(event__category=category)
        return qs.count()
    return 0


def evaluate_awards(student, *, by=None) -> list[StudentAward]:
    """Grant every award ``student`` now qualifies for and doesn't already
    hold. Called right when the points or counts behind a rule change."""
    granted = []
    held = set(StudentAward.objects.filter(student=student, ended_on__isnull=True).values_list("award_id", flat=True))
    rules = (AwardRule.objects.filter(organization_id=student.organization_id, is_active=True)
            .exclude(award_id__in=held).select_related("award", "category"))
    with transaction.atomic():
        for rule in rules:
            if rule.award_id in held:
                continue
            if _count_for(student, rule.threshold_kind, rule.category) < rule.threshold_value:
                continue
            grant = StudentAward.objects.create(
                organization_id=student.organization_id, student=student, award=rule.award, rule=rule,
                awarded_at=timezone.now(),
            )
            log(AuditLog.Action.CREATE, instance=grant, module=MODULE, actor=by,
                metadata={"rule": rule.pk, "award": rule.award.name})
            granted.append(grant)
            held.add(rule.award_id)
    return granted


def grant_award(student, award, *, note: str = "", by=None) -> StudentAward:
    if StudentAward.objects.filter(student=student, award=award, ended_on__isnull=True).exists():
        raise ConflictError("The student already holds this award.", code="already_held")
    grant = StudentAward.objects.create(
        organization_id=student.organization_id, student=student, award=award, awarded_at=timezone.now(),
        note=note, granted_by=by,
    )
    log(AuditLog.Action.CREATE, instance=grant, module=MODULE, actor=by, metadata={"manual": True})
    return grant


def end_award(grant: StudentAward, *, by=None) -> StudentAward:
    if grant.ended_on is not None:
        raise ConflictError("Already ended.", code="already_ended")
    grant.ended_on = timezone.now()
    grant.save(update_fields=["ended_on", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=grant, module=MODULE, actor=by,
        changes={"ended_on": {"before": None, "after": str(grant.ended_on)}})
    return grant
