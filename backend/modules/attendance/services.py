"""The attendance engine. Every way of taking attendance — a teacher's app,
the office, a QR scan, a biometric reader, the API — ends up here, so the
rules are the same whichever way it comes in.
"""
from dataclasses import dataclass
from datetime import date as Date, datetime, time as Time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.permissions.selectors import campus_ids_with_permission
from modules.academics.selectors import campus_closed_on, closure_on, students_taking
from modules.staff.selectors import staff_member_for_user
from modules.students.models import Enrollment
from modules.students.selectors import get_current_enrollment, student_for_user
from modules.timetable.models import TimetableEntry
from modules.timetable.services import lessons_on

from . import qr
from .models import (
    AttendanceCorrection,
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    BiometricIdentity,
    Punch,
    Source,
    StaffAttendanceDay,
    StaffWorkSchedule,
    WorkSchedule,
)

VIEW, MARK, MANAGE, DEVICES = ("attendance.view", "attendance.mark", "attendance.manage",
                               "attendance.devices")
MODULE = "attendance"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def org_timezone(organization) -> ZoneInfo:
    try:
        return ZoneInfo(organization.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def org_today(organization) -> Date:
    """Today where the organization is, not where the server is."""
    return timezone.localdate(timezone=org_timezone(organization))


def local_date(moment: datetime, organization) -> Date:
    return timezone.localtime(moment, org_timezone(organization)).date()


def day_bounds(day: Date, organization) -> tuple[datetime, datetime]:
    tz = org_timezone(organization)
    start = datetime.combine(day, Time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def holds(user, code: str, campus_id) -> bool:
    """Does ``user`` hold ``code`` at that campus (or organization-wide)?"""
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


# ---------------------------------------------------------------------------
# Who is expected
# ---------------------------------------------------------------------------
def expected_enrollments(session):
    """The enrollments that should be in ``session``: everyone in the class
    that day for a roll call; for a lesson, only students who take its
    subject (electives count only those who chose them)."""
    enrollments = Enrollment.objects.on(session.date).filter(section_id=session.section_id)
    if session.kind == AttendanceSession.Kind.LESSON:
        subject_id = session.timetable_entry.teaching_assignment.subject_id
        students = students_taking(session.section, subject_id, on=session.date)
        enrollments = enrollments.filter(student__in=students)
    return enrollments.select_related("student")


def lesson_on(entry, day):
    """The lesson ``entry`` gives on ``day``, changes and calendar applied,
    or None when it doesn't run that day."""
    lessons = lessons_on(day, organization_id=entry.organization_id,
                         entries=TimetableEntry.objects.filter(pk=entry.pk))
    return lessons[0] if lessons else None


def school_day_problem(section, day) -> str | None:
    """Why ``section`` has no roll call on ``day``, or None if it does.

    A calendar closure stops it. A section with a timetable needs a lesson
    that day (so weekends and make-up days follow the timetable); a school
    that keeps no timetable can take attendance any day it's open."""
    closure = closure_on(section, day)
    if closure is not None:
        return f"No classes that day: {closure.title}."
    if TimetableEntry.objects.filter(teaching_assignment__section=section).exists():
        lessons = lessons_on(day, organization_id=section.organization_id, section=section)
        if not any(not lesson.is_cancelled for lesson in lessons):
            return "The class has no lessons that day."
    return None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def open_session(*, day: Date, section=None, timetable_entry=None,
                 by=None) -> tuple[AttendanceSession, bool]:
    """The session for a lesson or a class's day, created on first use.

    Idempotent: a second call (another tap, a retry, a colleague) returns the
    same session. With ``by``, only someone who may take it can open it."""
    if timetable_entry is not None:
        section = timetable_entry.teaching_assignment.section
        kind = AttendanceSession.Kind.LESSON
    else:
        kind = AttendanceSession.Kind.DAILY
    if day > org_today(section.organization):
        raise ServiceError("Attendance can't be taken for a future date.", code="future_date")
    year = section.academic_year
    if not year.start_date <= day <= year.end_date:
        raise ServiceError("That date is outside the class's academic year.", code="outside_year")

    mode = section.program.attendance_mode
    if mode != kind:
        raise ServiceError(
            f"{section.program.name} takes attendance "
            f"{'in every lesson' if mode == 'lesson' else 'once a day'}.",
            code="wrong_mode",
        )

    lookup = ({"timetable_entry": timetable_entry, "date": day, "kind": kind} if timetable_entry
              else {"section": section, "date": day, "kind": kind})
    existing = AttendanceSession.objects.filter(**lookup).first()
    if existing is not None:
        if by is not None:
            ensure_can_take(by, existing)
        return existing, False

    if timetable_entry is not None:
        lesson = lesson_on(timetable_entry, day)
        if lesson is None:
            raise ServiceError("That lesson isn't on the timetable that day.", code="no_lesson")
        if lesson.is_cancelled:
            reason = lesson.closed_by.title if lesson.closed_by else "the lesson is cancelled"
            raise ConflictError(f"No attendance: {reason}.", code="lesson_cancelled")
        teacher = lesson.teacher
    else:
        problem = school_day_problem(section, day)
        if problem:
            raise ConflictError(problem, code="no_classes")
        teacher = section.class_teacher

    session = AttendanceSession(organization_id=section.organization_id, campus_id=section.campus_id,
                                teacher=teacher, **{"section": section, **lookup})
    if by is not None:
        ensure_can_take(by, session)
    try:
        with transaction.atomic():
            session.save()
    except IntegrityError:  # opened by someone else a moment ago
        return AttendanceSession.objects.get(**lookup), False
    return session, True


def can_take(user, session) -> bool:
    """The office (``attendance.manage`` at the campus) may take any session.
    Otherwise it's the day's teacher — a substitute, not the regular teacher
    they cover — or, for a roll call, the class teacher."""
    if holds(user, MANAGE, session.campus_id):
        return True
    if not holds(user, MARK, session.campus_id):
        return False
    staff = staff_member_for_user(user)
    if staff is None:
        return False
    if session.teacher_id == staff.pk:
        return True
    return session.kind == AttendanceSession.Kind.DAILY and session.section.class_teacher_id == staff.pk


def ensure_can_take(user, session) -> None:
    if not can_take(user, session):
        raise PermissionDeniedError("Only this class's teacher that day, or the office, can take "
                                    "this attendance.", code="not_your_class")


def _lock(session) -> AttendanceSession:
    return AttendanceSession.objects.select_for_update().get(pk=session.pk)


@dataclass
class Mark:
    enrollment_id: int
    status: str
    note: str = ""
    client_key: str = ""
    recorded_at: datetime | None = None


def mark(*, session, marks: list[Mark], rest: str | None = None, by=None,
         source: str = Source.TEACHER) -> list[AttendanceRecord]:
    """Record several students at once, and optionally everyone not yet
    marked as ``rest`` ("all present, except …").

    Re-marking before submission just updates the record. A mark whose
    ``client_key`` was already stored is a retried sync and is skipped."""
    with transaction.atomic():
        session = _lock(session)
        if session.is_submitted:
            raise ConflictError("This attendance is already submitted. Correct individual records "
                                "instead, with a reason.", code="session_submitted")
        expected = {e.pk: e for e in expected_enrollments(session)}
        existing = {r.enrollment_id: r for r in session.records.all()}
        keys = [m.client_key for m in marks if m.client_key]
        seen = set(AttendanceRecord.objects.filter(organization_id=session.organization_id,
                                                   client_key__in=keys)
                   .values_list("client_key", flat=True)) if keys else set()

        unknown = sorted({m.enrollment_id for m in marks} - set(expected))
        if unknown:
            raise ServiceError("Some students aren't expected in this class that day.",
                               code="not_expected", details={"enrollments": unknown})

        now = timezone.now()
        for m in marks:
            if m.client_key and m.client_key in seen:
                continue
            record = existing.get(m.enrollment_id)
            if record is None:
                record = AttendanceRecord(organization_id=session.organization_id, session=session,
                                          enrollment_id=m.enrollment_id)
                existing[m.enrollment_id] = record
            record.status, record.note, record.source = m.status, m.note, source
            record.marked_by, record.recorded_at = by, m.recorded_at or now
            if m.client_key:
                record.client_key = m.client_key
            record.save()

        if rest is not None:
            for enrollment_id in set(expected) - set(existing):
                existing[enrollment_id] = AttendanceRecord.objects.create(
                    organization_id=session.organization_id, session=session,
                    enrollment_id=enrollment_id, status=rest, source=source, marked_by=by,
                    recorded_at=now,
                )
    return list(session.records.select_related("enrollment__student"))


def submit(*, session, rest: str | None = None, by=None) -> AttendanceSession:
    """Close the session. Everyone expected must be marked, or ``rest``
    says what the others are. Submitting twice is harmless."""
    with transaction.atomic():
        session = _lock(session)
        if session.is_submitted:
            return session
        marked = set(session.records.values_list("enrollment_id", flat=True))
        missing = [e for e in expected_enrollments(session) if e.pk not in marked]
        if missing and rest is None:
            raise ConflictError(
                f"{len(missing)} student(s) aren't marked yet. Mark them, or say what the rest are.",
                code="unmarked",
                details={"enrollments": [{"id": e.pk, "student": e.student_id,
                                          "name": e.student.full_name} for e in missing]},
            )
        now = timezone.now()
        for enrollment in missing:
            AttendanceRecord.objects.create(
                organization_id=session.organization_id, session=session, enrollment=enrollment,
                status=rest, source=Source.TEACHER, marked_by=by, recorded_at=now,
            )
        session.status, session.submitted_at, session.submitted_by = (
            AttendanceSession.Status.SUBMITTED, now, by)
        session.save(update_fields=["status", "submitted_at", "submitted_by", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=session, module=MODULE, actor=by,
        changes={"status": {"before": "open", "after": "submitted"}})
    return session


def reopen(*, session, by=None) -> AttendanceSession:
    with transaction.atomic():
        session = _lock(session)
        if not session.is_submitted:
            return session
        session.status, session.submitted_at, session.submitted_by = (
            AttendanceSession.Status.OPEN, None, None)
        session.save(update_fields=["status", "submitted_at", "submitted_by", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=session, module=MODULE, actor=by,
        changes={"status": {"before": "submitted", "after": "open"}})
    return session


def correct(*, record, status: str, reason: str = "", note: str | None = None,
            by=None) -> AttendanceRecord:
    """Change one record. After submission this is a correction: the reason
    is required and the old status is kept in the record's history."""
    with transaction.atomic():
        record = AttendanceRecord.objects.select_for_update().select_related("session").get(pk=record.pk)
        old = record.status
        if record.session.is_submitted and old != status:
            if not reason.strip():
                raise ServiceError("Give a reason for changing submitted attendance.",
                                   code="reason_required")
            AttendanceCorrection.objects.create(
                organization_id=record.organization_id, record=record, old_status=old,
                new_status=status, reason=reason.strip(), corrected_by=by,
            )
            log(AuditLog.Action.UPDATE, instance=record, module=MODULE, actor=by,
                changes={"status": {"before": old, "after": status}},
                metadata={"correction": True, "reason": reason.strip()})
        record.status = status
        if note is not None:
            record.note = note
        record.marked_by = by
        record.save(update_fields=["status", "note", "marked_by", "updated_at"])
    return record


# ---------------------------------------------------------------------------
# QR
# ---------------------------------------------------------------------------
def issue_session_qr(*, session, ttl=qr.DEFAULT_TTL, late_after=None, latitude=None,
                     longitude=None, radius=None) -> tuple[str, float]:
    if session.is_submitted:
        raise ConflictError("This attendance is already submitted.", code="session_submitted")
    return qr.issue("session", organization_id=session.organization_id, target_id=session.pk,
                    ttl=ttl, late_after=late_after.timestamp() if late_after else None,
                    latitude=latitude, longitude=longitude, radius=radius)


def scan(*, token: str, user, latitude=None, longitude=None, device_id="") -> tuple[AttendanceRecord, bool]:
    """A student scans the code on the teacher's screen.

    Who they are comes from their login: user → student → enrollment on the
    session's date. Nothing in the request can name another student.
    Returns the record and whether it was new (False: already marked)."""
    token_ = qr.read(token, "session")
    if token_.organization_id != user.organization_id:
        raise ServiceError("This isn't a valid attendance QR code.", code="invalid_qr")
    session = AttendanceSession.objects.filter(pk=token_.target_id,
                                               organization_id=user.organization_id).first()
    if session is None:
        raise ServiceError("This isn't a valid attendance QR code.", code="invalid_qr")
    student = student_for_user(user)
    if student is None:
        raise PermissionDeniedError("Only a student account can scan attendance codes.",
                                    code="not_a_student")
    enrollment = expected_enrollments(session).filter(student=student).first()
    if enrollment is None:
        raise PermissionDeniedError("You aren't expected in this class.", code="not_in_class")
    qr.check_location(token_, latitude, longitude)

    with transaction.atomic():
        session = _lock(session)
        if session.is_submitted:
            raise ConflictError("This attendance is already submitted.", code="session_submitted")
        existing = session.records.filter(enrollment=enrollment).first()
        if existing is not None:
            return existing, False
        if device_id and session.records.filter(device_id=device_id).exists():
            raise PermissionDeniedError("This phone has already been used to mark someone in "
                                        "this class.", code="device_used")
        now = timezone.now()
        late = token_.late_after is not None and now.timestamp() > token_.late_after
        record = AttendanceRecord.objects.create(
            organization_id=session.organization_id, session=session, enrollment=enrollment,
            status=AttendanceStatus.LATE if late else AttendanceStatus.PRESENT,
            source=Source.QR, marked_by=user, recorded_at=now, device_id=device_id,
        )
    return record, True


def issue_staff_qr(*, campus, ttl=qr.DEFAULT_TTL, latitude=None, longitude=None,
                   radius=None) -> tuple[str, float]:
    return qr.issue("staff", organization_id=campus.organization_id, target_id=campus.pk,
                    ttl=ttl, latitude=latitude, longitude=longitude, radius=radius)


def staff_scan(*, token: str, user, latitude=None, longitude=None) -> tuple[Punch, bool]:
    """A staff member scans the code shown at their campus's gate."""
    from core.organizations.models import Campus

    token_ = qr.read(token, "staff")
    campus = Campus.objects.filter(pk=token_.target_id, organization_id=user.organization_id).first()
    if campus is None:
        raise ServiceError("This isn't a valid attendance QR code.", code="invalid_qr")
    staff = staff_member_for_user(user)
    if staff is None:
        raise PermissionDeniedError("Only a staff account can check in.", code="not_staff")
    qr.check_location(token_, latitude, longitude)
    now = timezone.now()
    # One scan per person per minute: a double tap isn't two punches.
    return record_punch(
        organization=campus.organization, campus=campus, staff=staff, punched_at=now,
        source=Source.QR, marked_by=user, dedupe_key=f"qr:{staff.pk}:{now:%Y%m%d%H%M}",
    )


# ---------------------------------------------------------------------------
# Punches and staff days
# ---------------------------------------------------------------------------
def identity_for_pin(organization_id, pin):
    return BiometricIdentity.objects.filter(organization_id=organization_id, pin=pin).first()


def record_punch(*, organization, campus, punched_at: datetime, source: str, dedupe_key: str,
                 pin: str = "", staff=None, student=None, device=None,
                 direction: str = Punch.Direction.UNKNOWN, verify: str = "", marked_by=None,
                 note: str = "") -> tuple[Punch, bool]:
    """Store one punch and apply it. The same event sent again (a device
    retrying, a double scan) is recognised by ``dedupe_key`` and ignored.

    A PIN nobody is mapped to yet is still stored, so the punches count once
    the person is mapped (see ``apply_unmapped_punches``)."""
    if staff is None and student is None and pin:
        identity = identity_for_pin(organization.pk, pin)
        if identity is not None:
            staff, student = identity.staff, identity.student
    try:
        with transaction.atomic():
            punch = Punch.objects.create(
                organization=organization, campus=campus, device=device, pin=pin, staff=staff,
                student=student, punched_at=punched_at, direction=direction, source=source,
                verify=verify, marked_by=marked_by, note=note, dedupe_key=dedupe_key,
            )
    except IntegrityError:
        return Punch.objects.get(organization=organization, dedupe_key=dedupe_key), False
    apply_punch(punch)
    return punch, True


def apply_punch(punch) -> None:
    day = local_date(punch.punched_at, punch.organization)
    if punch.staff_id is not None:
        rebuild_staff_day(punch.staff, day)
    elif punch.student_id is not None:
        mark_from_gate(punch.student, day, punch)


def apply_unmapped_punches(identity) -> int:
    """After a PIN is mapped, attach and apply the punches it already sent."""
    punches = list(Punch.objects.filter(organization_id=identity.organization_id, pin=identity.pin,
                                        staff__isnull=True, student__isnull=True))
    for punch in punches:
        punch.staff_id, punch.student_id = identity.staff_id, identity.student_id
        punch.save(update_fields=["staff", "student"])
        apply_punch(punch)
    return len(punches)


def mark_from_gate(student, day: Date, punch) -> AttendanceRecord | None:
    """A student's punch at the gate counts as present in their class's
    daily roll call — if their program takes one, the roll call isn't
    submitted, and nobody has marked them yet. A teacher's mark always wins.
    Lesson attendance isn't touched: being at the gate isn't being in class."""
    enrollment = get_current_enrollment(student, on=day)
    if enrollment is None or enrollment.section is None:
        return None
    try:
        session, _ = open_session(day=day, section=enrollment.section)
    except ServiceError:
        return None
    with transaction.atomic():
        session = _lock(session)
        if session.is_submitted or session.records.filter(enrollment=enrollment).exists():
            return None
        return AttendanceRecord.objects.create(
            organization_id=session.organization_id, session=session, enrollment=enrollment,
            status=AttendanceStatus.PRESENT, source=Source.BIOMETRIC, recorded_at=punch.punched_at,
        )


def schedule_for(staff) -> WorkSchedule | None:
    own = StaffWorkSchedule.objects.filter(staff=staff).select_related("schedule").first()
    if own is not None:
        return own.schedule
    return WorkSchedule.objects.filter(campus_id=staff.campus_id, is_default=True).first()


def rebuild_staff_day(staff, day: Date) -> StaffAttendanceDay | None:
    """Work a staff member's day out again from their punches: first punch
    in, last punch out, late against their schedule, half day if they left
    early. A day the office set by hand is left alone."""
    organization = staff.organization
    current = StaffAttendanceDay.objects.filter(staff=staff, date=day).first()
    if current is not None and current.is_override:
        return current
    start, end = day_bounds(day, organization)
    times = list(Punch.objects.filter(staff=staff, punched_at__gte=start, punched_at__lt=end)
                 .order_by("punched_at").values_list("punched_at", flat=True))
    if not times:
        return current
    first_in = times[0]
    last_out = times[-1] if len(times) > 1 else None
    worked = int((last_out - first_in).total_seconds() // 60) if last_out else None

    status = StaffAttendanceDay.Status.PRESENT
    schedule = schedule_for(staff)
    if schedule is not None:
        tz = org_timezone(organization)
        late_from = datetime.combine(day, schedule.start_time, tzinfo=tz) + timedelta(
            minutes=schedule.grace_minutes)
        if worked is not None and worked < schedule.half_day_minutes:
            status = StaffAttendanceDay.Status.HALF_DAY
        elif first_in > late_from:
            status = StaffAttendanceDay.Status.LATE

    values = {"organization_id": staff.organization_id, "status": status, "first_in": first_in,
              "last_out": last_out, "worked_minutes": worked}
    staff_day, _ = StaffAttendanceDay.objects.update_or_create(staff=staff, date=day, defaults=values)
    return staff_day


def set_staff_day(*, staff, day: Date, status: str, note: str = "", by=None) -> StaffAttendanceDay:
    """The office sets a day by hand (leave, on duty, a forgotten punch).
    Punches no longer change it; ``clear_staff_day_override`` undoes that."""
    staff_day = StaffAttendanceDay.objects.filter(staff=staff, date=day).first()
    before = staff_day.status if staff_day else None
    staff_day, _ = StaffAttendanceDay.objects.update_or_create(
        staff=staff, date=day,
        defaults={"organization_id": staff.organization_id, "status": status, "note": note,
                  "is_override": True, "set_by": by},
    )
    log(AuditLog.Action.UPDATE, instance=staff_day, module=MODULE, actor=by,
        changes={"status": {"before": before, "after": status}}, metadata={"note": note})
    return staff_day


def clear_staff_day_override(*, staff_day, by=None) -> StaffAttendanceDay | None:
    """Hand the day back to the punches. With none, it has no record again."""
    staff, day = staff_day.staff, staff_day.date
    start, end = day_bounds(day, staff.organization)
    log(AuditLog.Action.UPDATE, instance=staff_day, module=MODULE, actor=by,
        changes={"is_override": {"before": True, "after": False}})
    if not Punch.objects.filter(staff=staff, punched_at__gte=start, punched_at__lt=end).exists():
        staff_day.delete()
        return None
    staff_day.is_override = False
    staff_day.save(update_fields=["is_override", "updated_at"])
    return rebuild_staff_day(staff, day)


def works_on(staff, day: Date, schedule=None) -> bool:
    """Is ``day`` one of ``staff``'s working days? Their schedule's weekdays
    (Sunday–Friday when they have none), inside their employment. The
    calendar isn't checked; see ``staff_expected_on``."""
    if staff.joined_on and day < staff.joined_on:
        return False
    if staff.left_on and day >= staff.left_on:
        return False
    weekdays = schedule.weekdays if schedule is not None and schedule.weekdays else [7, 1, 2, 3, 4, 5]
    return day.isoweekday() in weekdays


def staff_expected_on(staff, day: Date) -> bool:
    """A working day, and the campus isn't closed."""
    return works_on(staff, day, schedule_for(staff)) and campus_closed_on(staff.campus, day) is None
