"""Staff writes made by other modules. The office adds staff through the
API (``StaffMemberSerializer``); careers hires through here, with the same
rules: an employee number unused in the organization, a campus of the
same organization, and a login not linked to anyone else."""
from django.db import transaction

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, ServiceError

from .models import StaffMember


@transaction.atomic
def create_staff_member(*, campus, employee_number: str, first_name: str, last_name: str, by=None, user=None,
                        **fields) -> StaffMember:
    employee_number = employee_number.strip()
    if not employee_number:
        raise ServiceError("An employee number is required.", code="employee_number_required")
    if StaffMember.objects.filter(organization_id=campus.organization_id, employee_number=employee_number).exists():
        raise ConflictError("This employee number is already in use in this organization.",
                            code="duplicate_number")
    if user is not None:
        if user.organization_id != campus.organization_id:
            raise ServiceError("Unknown user.", code="invalid_user")
        if StaffMember.all_objects.filter(user=user).exists():
            raise ConflictError("This login already belongs to a staff member.", code="user_taken")
    staff = StaffMember.objects.create(organization_id=campus.organization_id, campus=campus, user=user,
                                       employee_number=employee_number, first_name=first_name,
                                       last_name=last_name, **fields)
    log(AuditLog.Action.CREATE, instance=staff, module="staff", actor=by)
    return staff
