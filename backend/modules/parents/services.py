"""Write operations for parents and their links to students."""
from django.db import transaction

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, ServiceError

from .models import Parent, StudentParent


@transaction.atomic
def create_parent(*, organization_id: int, created_by=None, **fields) -> Parent:
    parent = Parent(organization_id=organization_id, **fields)
    parent.full_clean()
    parent.save()
    log(AuditLog.Action.CREATE, instance=parent, module="parents", actor=created_by)
    return parent


@transaction.atomic
def link_student(
    *, parent: Parent, student, relationship: str, is_primary_contact: bool = False, by=None
) -> StudentParent:
    """Link ``parent`` to ``student``.

    Making someone the primary contact takes it from whoever had it, in the
    same transaction, so a student never has two.
    """
    if student.organization_id != parent.organization_id:
        raise ServiceError("Student belongs to a different organization.", code="invalid_student")
    if StudentParent.objects.filter(student=student, parent=parent).exists():
        raise ConflictError("This parent is already linked to the student.", code="already_linked")

    if is_primary_contact:
        # Lock the student's links so two concurrent "make primary" requests
        # cannot both succeed.
        list(StudentParent.objects.select_for_update().filter(student=student))
        StudentParent.objects.filter(student=student, is_primary_contact=True).update(
            is_primary_contact=False
        )

    link = StudentParent.objects.create(
        organization_id=parent.organization_id,
        student=student,
        parent=parent,
        relationship=relationship,
        is_primary_contact=is_primary_contact,
    )
    log(
        AuditLog.Action.UPDATE,
        instance=parent,
        module="parents",
        actor=by,
        metadata={
            "operation": "link_student",
            "student": student.pk,
            "relationship": relationship,
            "is_primary_contact": is_primary_contact,
        },
    )
    return link


@transaction.atomic
def unlink_student(*, parent: Parent, student, by=None) -> None:
    deleted, _ = StudentParent.objects.filter(student=student, parent=parent).delete()
    if not deleted:
        raise ServiceError("This parent is not linked to the student.", code="not_linked")
    log(
        AuditLog.Action.UPDATE,
        instance=parent,
        module="parents",
        actor=by,
        metadata={"operation": "unlink_student", "student": student.pk},
    )
