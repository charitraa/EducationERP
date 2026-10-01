"""Read-side queries: whose beds a signed-in person may see."""
from django.db.models import Q

from modules.parents.selectors import links_for_parent, parent_for_user
from modules.staff.selectors import staff_member_for_user
from modules.students.selectors import student_for_user

from .models import Allocation


def allocations_for_user(user):
    """The caller's own stays (as a student or staff member) and, for a
    parent, their children's — current and past."""
    match = Q(pk__in=[])
    student = student_for_user(user)
    if student is not None:
        match |= Q(student=student)
    staff = staff_member_for_user(user)
    if staff is not None:
        match |= Q(staff=staff)
    parent = parent_for_user(user)
    if parent is not None:
        match |= Q(student_id__in=links_for_parent(parent).values("student_id"))
    return (Allocation.objects.filter(match, organization_id=user.organization_id)
            .select_related("bed__room__building", "student", "staff"))
