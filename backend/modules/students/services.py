"""Write operations for students and their enrollments.

Every change to where a student is or what state they are in goes through
here, so the student row and its enrollment history can never disagree:
an active or suspended student has exactly one open enrollment, at the
student's campus; a graduated or withdrawn student has none.
"""
from django.db import transaction
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log, snapshot
from core.common.exceptions import ConflictError, ServiceError

from .models import Enrollment, Student

# Which status a student may move to from each status. Graduated and
# withdrawn are final: a returning student comes back through a new admission.
ALLOWED_TRANSITIONS = {
    Student.Status.ACTIVE: {
        Student.Status.SUSPENDED,
        Student.Status.GRADUATED,
        Student.Status.WITHDRAWN,
    },
    Student.Status.SUSPENDED: {Student.Status.ACTIVE, Student.Status.WITHDRAWN},
    Student.Status.GRADUATED: set(),
    Student.Status.WITHDRAWN: set(),
}

# How a status change closes the open enrollment, if it does.
_CLOSING_STATUS = {
    Student.Status.GRADUATED: Enrollment.Status.COMPLETED,
    Student.Status.WITHDRAWN: Enrollment.Status.WITHDRAWN,
}


def _ensure_same_organization(organization_id, campus) -> None:
    if campus.organization_id != organization_id:
        raise ServiceError("Campus belongs to a different organization.", code="invalid_campus")


def _open_enrollment(student: Student) -> Enrollment:
    enrollment = (
        Enrollment.objects.select_for_update()
        .filter(student=student, status=Enrollment.Status.ACTIVE)
        .first()
    )
    if enrollment is None:  # pragma: no cover - guarded by the invariant above
        raise ConflictError("Student has no open enrollment.", code="no_open_enrollment")
    return enrollment


def _close(enrollment: Enrollment, status: str, on_date, reason: str) -> None:
    if on_date < enrollment.started_on:
        raise ServiceError(
            f"The date cannot be before the enrollment started ({enrollment.started_on}).",
            code="invalid_date",
        )
    enrollment.status = status
    enrollment.ended_on = on_date
    enrollment.end_reason = reason
    enrollment.save(update_fields=["status", "ended_on", "end_reason", "updated_at"])


@transaction.atomic
def create_student(
    *,
    organization_id: int,
    campus,
    student_number: str,
    created_by=None,
    **fields,
) -> Student:
    """Create a student and open their first enrollment at ``campus``."""
    _ensure_same_organization(organization_id, campus)

    student = Student(
        organization_id=organization_id,
        campus=campus,
        student_number=student_number,
        **fields,
    )
    # full_clean runs the partial unique constraints in Python, so a duplicate
    # student number is a 400 on every database — MySQL included, which
    # cannot enforce them itself.
    student.full_clean()
    student.save()

    Enrollment.objects.create(
        organization_id=organization_id,
        student=student,
        campus=campus,
        started_on=student.admitted_on,
    )

    log(AuditLog.Action.CREATE, instance=student, module="students", actor=created_by)
    return student


@transaction.atomic
def transfer_student(*, student: Student, to_campus, on_date=None, reason="", by=None) -> Student:
    """Move a student to another campus of the same organization."""
    student = Student.objects.select_for_update().get(pk=student.pk)
    _ensure_same_organization(student.organization_id, to_campus)
    if not student.is_enrolled:
        raise ConflictError(
            f"A {student.get_status_display().lower()} student cannot be transferred.",
            code="invalid_status",
        )
    if to_campus.pk == student.campus_id:
        raise ServiceError("The student is already at this campus.", code="same_campus")

    on_date = on_date or timezone.localdate()
    before = snapshot(student)

    _close(_open_enrollment(student), Enrollment.Status.TRANSFERRED, on_date, reason)
    Enrollment.objects.create(
        organization_id=student.organization_id,
        student=student,
        campus=to_campus,
        started_on=on_date,
    )
    student.campus = to_campus
    student.save(update_fields=["campus", "updated_at"])

    log(
        AuditLog.Action.UPDATE,
        instance=student,
        module="students",
        actor=by,
        changes={"campus": {"before": before["campus"], "after": to_campus.pk}},
        metadata={"operation": "transfer", "reason": reason, "on": str(on_date)},
    )
    return student


@transaction.atomic
def change_student_status(
    *, student: Student, status: str, on_date=None, reason="", by=None
) -> Student:
    """Suspend, reactivate, graduate or withdraw a student."""
    student = Student.objects.select_for_update().get(pk=student.pk)
    if status not in ALLOWED_TRANSITIONS.get(student.status, set()):
        raise ConflictError(
            f"Cannot change a student from {student.status} to {status}.",
            code="invalid_transition",
        )

    before = student.status
    if status in _CLOSING_STATUS:
        _close(
            _open_enrollment(student),
            _CLOSING_STATUS[status],
            on_date or timezone.localdate(),
            reason,
        )

    student.status = status
    student.save(update_fields=["status", "updated_at"])

    log(
        AuditLog.Action.UPDATE,
        instance=student,
        module="students",
        actor=by,
        changes={"status": {"before": before, "after": status}},
        metadata={"operation": "status_change", "reason": reason},
    )
    return student


@transaction.atomic
def place_student(*, student: Student, section, on_date=None, reason="", by=None) -> Student:
    """Put a student in a section (class group), or move them to another.

    The first placement of an enrollment fills in its section. Any later move —
    promotion to the next grade, a new academic year, a section change —
    closes the current enrollment as ``moved`` and opens a new one, so the
    history shows every class the student has been in.
    """
    student = Student.objects.select_for_update().get(pk=student.pk)
    if section.organization_id != student.organization_id:
        raise ServiceError("Section belongs to a different organization.", code="invalid_section")
    if not student.is_enrolled:
        raise ConflictError(
            f"A {student.get_status_display().lower()} student cannot be placed.",
            code="invalid_status",
        )
    if section.campus_id != student.campus_id:
        raise ServiceError(
            "The section is at another campus. Transfer the student first.",
            code="different_campus",
        )

    # Placing ahead into next year's sections is normal (promotions are set
    # up before the year starts); placing into a year that is already over
    # is always a mistake.
    effective = on_date or timezone.localdate()
    year = section.academic_year
    if year.end_date < effective:
        raise ServiceError(
            f"The academic year {year.name} ended on {year.end_date}.", code="year_ended"
        )

    current = _open_enrollment(student)
    if current.section_id == section.pk:
        raise ServiceError("The student is already in this section.", code="same_section")

    before = current.section_id
    if current.section_id is None:
        current.section = section
        current.save(update_fields=["section", "updated_at"])
    else:
        on_date = on_date or timezone.localdate()
        _close(current, Enrollment.Status.MOVED, on_date, reason)
        Enrollment.objects.create(
            organization_id=student.organization_id,
            student=student,
            campus_id=student.campus_id,
            section=section,
            started_on=on_date,
        )

    log(
        AuditLog.Action.UPDATE,
        instance=student,
        module="students",
        actor=by,
        changes={"section": {"before": before, "after": section.pk}},
        metadata={"operation": "place", "reason": reason},
    )
    return student
