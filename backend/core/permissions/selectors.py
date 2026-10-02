"""Read-side queries for the RBAC system.

Kept separate from services (write side) so callers can see at a glance which
functions touch data and which only read it.
"""
from django.db.models import Q
from django.utils import timezone

from .models import Permission, Role, UserRole

# Cached on the user instance for the life of one request.
_CACHE_ATTR = "_permission_code_cache"


def active_role_assignments(user, campus=None):
    """Role assignments that currently apply to ``user``.

    Expired assignments are excluded. When ``campus`` is given, the result is
    organization-wide assignments plus assignments scoped to that campus.
    """
    now = timezone.now()
    qs = (
        UserRole.objects.filter(user=user)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .filter(role__deleted_at__isnull=True)
        .select_related("role", "campus")
    )
    if campus is not None:
        campus_id = getattr(campus, "pk", campus)
        qs = qs.filter(Q(campus__isnull=True) | Q(campus_id=campus_id))
    return qs


def get_user_permission_codes(user, campus=None) -> set[str]:
    """Every permission code held by ``user``, resolved through their roles."""
    if not user or not user.is_authenticated or not user.is_active:
        return set()

    # Platform superusers bypass RBAC entirely.
    if user.is_superuser:
        from .registry import permission_codes

        return permission_codes() | set(
            Permission.objects.values_list("code", flat=True)
        )

    if campus is None and hasattr(user, _CACHE_ATTR):
        return getattr(user, _CACHE_ATTR)

    assignments = active_role_assignments(user, campus=campus)
    codes = set(
        Permission.objects.filter(roles__assignments__in=assignments)
        .distinct()
        .values_list("code", flat=True)
    )

    if campus is None:
        setattr(user, _CACHE_ATTR, codes)
    return codes


def organization_wide_permission_codes(user) -> set[str]:
    """Codes held through assignments that are not narrowed to a campus.

    What a user may do *everywhere* in their organization, as opposed to
    ``get_user_permission_codes(user)``, which merges every assignment.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return set()
    if user.is_superuser:
        return get_user_permission_codes(user)

    assignments = active_role_assignments(user).filter(campus__isnull=True)
    return set(
        Permission.objects.filter(roles__assignments__in=assignments)
        .distinct()
        .values_list("code", flat=True)
    )


def campus_ids_with_permission(user, code: str) -> set[int] | None:
    """Where ``user`` holds ``code``.

    ``None`` means everywhere: the permission comes from an organization-wide
    assignment (or the user is a superuser). Otherwise the ids of the campuses
    whose scoped assignments carry it — possibly empty.
    """
    if not user or not user.is_authenticated or not user.is_active:
        return set()
    if user.is_superuser:
        return None

    campus_ids = set(
        active_role_assignments(user)
        .filter(role__permissions__code=code)
        .values_list("campus_id", flat=True)
    )
    if None in campus_ids:
        return None
    return campus_ids


def user_has_permission(user, code: str, campus=None) -> bool:
    return code in get_user_permission_codes(user, campus=campus)


def user_has_any_permission(user, codes, campus=None) -> bool:
    held = get_user_permission_codes(user, campus=campus)
    return any(code in held for code in codes)


def clear_permission_cache(user) -> None:
    """Drop the per-request cache after a role change."""
    if hasattr(user, _CACHE_ATTR):
        delattr(user, _CACHE_ATTR)


def roles_available_to(organization_id):
    """System roles plus the roles owned by one organization.

    Note the explicit ``Q(organization__isnull=True)``: an ``__in`` lookup
    containing ``None`` compiles to ``IN (x, NULL)``, which never matches a
    NULL row, so system roles would silently disappear.
    """
    return Role.objects.filter(
        Q(organization__isnull=True) | Q(organization_id=organization_id)
    )


def visible_roles_for(user):
    """Roles a user is allowed to see: system roles plus their own org's."""
    qs = Role.objects.select_related("organization").prefetch_related("permissions")
    if user.is_platform_admin:
        return qs
    return qs.filter(
        Q(organization__isnull=True) | Q(organization_id=user.organization_id)
    )


def users_holding(codes, *, campus_id, organization_id):
    """Active users of the organization who hold any of ``codes`` at
    ``campus_id``, through an organization-wide role or one scoped to that
    campus. For deciding whom to notify."""
    from django.db.models import Q
    from django.utils import timezone

    from core.accounts.models import User

    from .models import UserRole

    now = timezone.now()
    role_ids = (UserRole.objects.filter(user__organization_id=organization_id, role__permissions__code__in=codes,
                                        role__deleted_at__isnull=True)
                .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
                .filter(Q(campus__isnull=True) | Q(campus_id=campus_id))
                .values("user_id"))
    # People only: an API key holding the permission has nobody to tell.
    return User.objects.filter(pk__in=role_ids, is_active=True).exclude(user_type=User.Type.INTEGRATION)
