"""Read-side queries: whose routes and boarding marks a signed-in person may see."""
from django.db.models import Q

from modules.parents.selectors import links_for_parent, parent_for_user
from modules.staff.selectors import staff_member_for_user
from modules.students.selectors import student_for_user

from .models import Assignment, TripRecord


def _riders(user) -> Q:
    """The caller as a student or staff rider, and a parent's children."""
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
    return match


def assignments_for_user(user):
    return (Assignment.objects.filter(_riders(user), organization_id=user.organization_id)
            .select_related("route", "stop", "student", "staff"))


def trip_records_for_user(user):
    return (TripRecord.objects.filter(assignment__in=assignments_for_user(user))
            .select_related("trip__route", "assignment__stop", "assignment__student", "assignment__staff")
            .order_by("-trip__date", "trip__direction", "pk"))
