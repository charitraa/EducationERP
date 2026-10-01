"""Read-side queries: who to alert, which assets someone holds."""
from django.db.models import Q

from core.permissions.selectors import users_holding  # noqa: F401  (used by services)

from .models import Asset, AssetAssignment


def assets_held_by(user):
    """Assets currently assigned to the staff member or student behind ``user``."""
    active = AssetAssignment.objects.filter(returned_on__isnull=True).filter(
        Q(staff__user=user) | Q(student__user=user))
    return Asset.objects.filter(pk__in=active.values("asset_id")).select_related("item", "store")
