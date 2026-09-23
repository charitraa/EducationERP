"""The admission workflow: decisions, and turning an applicant into a student.

Each step locks the admission row first, so two people deciding on the same
application at once cannot both succeed — and enrolling twice can never
create two students.
"""
from django.db import transaction
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, ServiceError

from .models import Admission

Status = Admission.Status

# Where each action may start from.
_ALLOWED_FROM = {
    "approve": {Status.PENDING},
    "reject": {Status.PENDING},
    "withdraw": {Status.PENDING, Status.APPROVED},
    "enroll": {Status.APPROVED},
}


def _lock(admission: Admission, operation: str) -> Admission:
    admission = Admission.objects.select_for_update().get(pk=admission.pk)
    if admission.status not in _ALLOWED_FROM[operation]:
        raise ConflictError(
            f"Cannot {operation} an admission that is {admission.status}.",
            code="invalid_transition",
        )
    return admission


def _decide(admission: Admission, status: str, note: str, by) -> Admission:
    before = admission.status
    admission.status = status
    admission.decided_at = timezone.now()
    admission.decided_by = by
    admission.decision_note = note
    admission.save(
        update_fields=["status", "decided_at", "decided_by", "decision_note", "updated_at"]
    )
    log(
        AuditLog.Action.UPDATE,
        instance=admission,
        module="admissions",
        actor=by,
        changes={"status": {"before": before, "after": status}},
        metadata={"note": note},
    )
    return admission


@transaction.atomic
def approve_admission(*, admission: Admission, note: str = "", by=None) -> Admission:
    return _decide(_lock(admission, "approve"), Status.APPROVED, note, by)


@transaction.atomic
def reject_admission(*, admission: Admission, note: str, by=None) -> Admission:
    if not note.strip():
        raise ServiceError("A reason is required to reject an application.", code="note_required")
    return _decide(_lock(admission, "reject"), Status.REJECTED, note, by)


@transaction.atomic
def withdraw_admission(*, admission: Admission, note: str = "", by=None) -> Admission:
    return _decide(_lock(admission, "withdraw"), Status.WITHDRAWN, note, by)


@transaction.atomic
def enroll_admission(
    *, admission: Admission, student_number: str, started_on=None, by=None
) -> Admission:
    """Create the student (and guardian, if given) from an approved application.

    All or nothing: if the student number is taken or anything else fails,
    no student, parent or status change is left behind.
    """
    from modules.parents.services import create_parent, link_student
    from modules.students.services import create_student

    admission = _lock(admission, "enroll")

    student = create_student(
        organization_id=admission.organization_id,
        campus=admission.campus,
        student_number=student_number,
        first_name=admission.first_name,
        middle_name=admission.middle_name,
        last_name=admission.last_name,
        date_of_birth=admission.date_of_birth,
        gender=admission.gender,
        email=admission.email,
        phone=admission.phone,
        address=admission.address,
        admitted_on=started_on or timezone.localdate(),
        created_by=by,
    )

    if admission.guardian_first_name:
        parent = create_parent(
            organization_id=admission.organization_id,
            first_name=admission.guardian_first_name,
            last_name=admission.guardian_last_name,
            phone=admission.guardian_phone,
            email=admission.guardian_email,
            created_by=by,
        )
        link_student(
            parent=parent,
            student=student,
            relationship=admission.guardian_relationship or "guardian",
            is_primary_contact=True,
            by=by,
        )

    admission.status = Status.ENROLLED
    admission.student = student
    admission.save(update_fields=["status", "student", "updated_at"])
    log(
        AuditLog.Action.UPDATE,
        instance=admission,
        module="admissions",
        actor=by,
        changes={"status": {"before": Status.APPROVED, "after": Status.ENROLLED}},
        metadata={"student": student.pk},
    )
    return admission
