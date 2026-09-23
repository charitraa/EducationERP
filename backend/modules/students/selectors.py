"""Read-side queries for students.

Other modules (parents, admissions, and later academics, attendance, finance)
read students through these functions, never by querying the tables.
"""
from django.db.models import Prefetch

from core.permissions.selectors import campus_ids_with_permission

from .models import Enrollment, Student


def with_current_enrollment(queryset):
    """Attach the open enrollment as ``current_enrollments`` (0 or 1 items)."""
    return queryset.prefetch_related(
        Prefetch(
            "enrollments",
            queryset=Enrollment.objects.filter(status=Enrollment.Status.ACTIVE).select_related(
                "campus", "section__program", "section__academic_year"
            ),
            to_attr="current_enrollments",
        )
    )


def get_current_enrollment(student: Student) -> Enrollment | None:
    prefetched = getattr(student, "current_enrollments", None)
    if prefetched is not None:
        return prefetched[0] if prefetched else None
    return (
        student.enrollments.filter(status=Enrollment.Status.ACTIVE)
        .select_related("campus", "section__program", "section__academic_year")
        .first()
    )


def student_for_user(user) -> Student | None:
    """The student record linked to a login account, if any."""
    if not user or not user.is_authenticated:
        return None
    return with_current_enrollment(
        Student.objects.select_related("campus").filter(user=user)
    ).first()


def students_visible_to(user, permission: str = "students.view"):
    """Students ``user`` may act on with ``permission``.

    Applies the same boundaries as the students API: the caller's organization,
    narrowed to the campuses their role covers when it is campus-scoped.
    """
    if not user or not user.is_authenticated:
        return Student.objects.none()

    qs = Student.objects.select_related("campus")
    if not user.is_platform_admin:
        if user.organization_id is None:
            return Student.objects.none()
        qs = qs.filter(organization_id=user.organization_id)

    campus_ids = campus_ids_with_permission(user, permission)
    if campus_ids is not None:
        qs = qs.filter(campus_id__in=campus_ids)
    return qs


def students_by_ids(ids):
    return Student.objects.select_related("campus").filter(pk__in=ids)


def students_in_section(section):
    """Students currently placed in ``section`` (open enrollments only)."""
    return Student.objects.select_related("campus").filter(
        enrollments__section=section, enrollments__status=Enrollment.Status.ACTIVE
    )
