"""Read-side: which notices a plain member of the organization gets to see."""
from django.db.models import Q
from django.utils import timezone

from modules.parents.selectors import links_for_parent, parent_for_user
from modules.staff.selectors import staff_member_for_user
from modules.students.selectors import student_for_user

from .models import Notice, NoticeAudience

_AUDIENCE_FOR_USER_TYPE = {"student": NoticeAudience.STUDENTS, "parent": NoticeAudience.PARENTS,
                           "alumni": NoticeAudience.ALUMNI}


def visible_to(user):
    """Published, unexpired notices matching ``user``'s audience and campus.

    A student/parent/staff profile narrows the campus; someone with none
    (e.g. an org-admin with no staff record) only sees org-wide notices —
    a manage-permission holder gets the fuller view in the viewset instead.
    """
    now = timezone.now()
    qs = Notice.objects.select_related("campus", "created_by").filter(
        organization_id=user.organization_id, published_at__isnull=False, published_at__lte=now,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gte=now))

    audience = _AUDIENCE_FOR_USER_TYPE.get(user.user_type, NoticeAudience.STAFF)
    qs = qs.filter(Q(audience=NoticeAudience.ALL) | Q(audience=audience))

    campus_ids = None
    student = student_for_user(user)
    if student is not None:
        campus_ids = {student.campus_id}
    else:
        parent = parent_for_user(user)
        if parent is not None:
            campus_ids = {link.student.campus_id for link in links_for_parent(parent)}
        else:
            staff = staff_member_for_user(user)
            if staff is not None:
                campus_ids = {staff.campus_id}

    if campus_ids is not None:
        qs = qs.filter(Q(campus__isnull=True) | Q(campus_id__in=campus_ids))
    else:
        qs = qs.filter(campus__isnull=True)
    return qs
