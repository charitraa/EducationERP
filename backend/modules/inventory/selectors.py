"""Read-side queries: who to alert, which assets someone holds."""
from django.db.models import Q
from django.utils import timezone

from core.accounts.models import User
from core.permissions.models import UserRole

from .models import Asset, AssetAssignment


def users_holding(codes, *, campus_id, organization_id):
    """Active users of the organization who hold any of ``codes`` at ``campus_id``
    (through an organization-wide role or one scoped to that campus)."""
    now = timezone.now()
    role_ids = (UserRole.objects.filter(user__organization_id=organization_id, role__permissions__code__in=codes,
                                        role__deleted_at__isnull=True)
                .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
                .filter(Q(campus__isnull=True) | Q(campus_id=campus_id))
                .values("user_id"))
    return User.objects.filter(pk__in=role_ids, is_active=True)


def assets_held_by(user):
    """Assets currently assigned to the staff member or student behind ``user``."""
    active = AssetAssignment.objects.filter(returned_on__isnull=True).filter(
        Q(staff__user=user) | Q(student__user=user))
    return Asset.objects.filter(pk__in=active.values("asset_id")).select_related("item", "store")
