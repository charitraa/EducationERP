"""Read-side queries for staff."""
from .models import StaffMember


def staff_member_for_user(user) -> StaffMember | None:
    if not user or not user.is_authenticated:
        return None
    return StaffMember.objects.select_related("campus").filter(user=user).first()
