"""Read-side queries for parents."""
from .models import Parent, StudentParent


def parent_for_user(user) -> Parent | None:
    if not user or not user.is_authenticated:
        return None
    return Parent.objects.filter(user=user).first()


def links_for_parent(parent: Parent, students=None):
    """The parent's links to students, optionally limited to ``students``
    (a queryset the caller is allowed to see)."""
    links = StudentParent.objects.filter(
        parent=parent, student__deleted_at__isnull=True
    ).select_related("student", "student__campus")
    if students is not None:
        links = links.filter(student__in=students)
    return links


def links_for_student(student):
    return StudentParent.objects.filter(student=student, parent__deleted_at__isnull=True).select_related("parent")
