"""Write operations for users.

Views and serializers call these; the business rules (tenant binding, role
assignment, audit entries) live here so later modules can reuse them without
going through HTTP.
"""
from django.db import transaction

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.permissions.models import Role, UserRole
from core.permissions.selectors import (
    clear_permission_cache,
    get_user_permission_codes,
    organization_wide_permission_codes,
    roles_available_to,
)

from .models import User


# ---------------------------------------------------------------------------
# Privilege-escalation guards
#
# Holding `users.manage_roles` or `users.update` must not be a path to more
# power than the caller already has. Without these, anyone who can assign
# roles could give themselves org-admin, and anyone who can reset passwords
# could take over the org-admin's account.
# ---------------------------------------------------------------------------
def ensure_can_grant(actor: User | None, role, campus=None) -> None:
    """``actor`` may only grant or revoke a role whose permissions they hold.

    Held *at the same scope*: an organization-wide grant needs the permissions
    organization-wide, a campus grant needs them organization-wide or at that
    campus. ``actor=None`` is the system itself (commands, other services).
    """
    if actor is None or actor.is_superuser:
        return

    held = (
        get_user_permission_codes(actor, campus=campus)
        if campus is not None
        else organization_wide_permission_codes(actor)
    )
    missing = set(role.permissions.values_list("code", flat=True)) - held
    if missing:
        raise PermissionDeniedError(
            "You can only grant or revoke roles whose permissions you hold "
            f"yourself. Missing: {', '.join(sorted(missing))}.",
            code="role_exceeds_own_permissions",
        )


def ensure_can_manage_user(actor: User | None, target: User) -> None:
    """``actor`` may only edit, reset or remove a user no more powerful.

    Compares everything the target can do with everything the actor can, so
    a campus admin can manage teachers and students but not the org-admin.
    """
    if actor is None or actor.is_superuser or actor.pk == target.pk:
        return
    if target.is_superuser:
        raise PermissionDeniedError(
            "Only a superuser can manage a superuser account.",
            code="target_exceeds_own_permissions",
        )

    missing = get_user_permission_codes(target) - get_user_permission_codes(actor)
    if missing:
        raise PermissionDeniedError(
            "You cannot manage a user who holds permissions you do not.",
            code="target_exceeds_own_permissions",
        )


@transaction.atomic
def create_user(
    *,
    email: str,
    password: str,
    created_by: User | None = None,
    organization=None,
    role_codes=(),
    **fields,
) -> User:
    """Create a user inside the creator's organization and assign roles."""
    if organization is None and created_by is not None:
        organization = created_by.organization

    email = email.lower().strip()
    if User.all_objects.filter(email=email).exists():
        raise ConflictError("A user with this email already exists.", code="email_taken")

    user = User.objects.create_user(
        email=email,
        password=password,
        organization=organization,
        **fields,
    )

    for code in role_codes:
        assign_role(user=user, role_code=code, granted_by=created_by)

    return user


@transaction.atomic
def assign_role(
    *, user: User, role=None, role_code: str | None = None, campus=None,
    granted_by: User | None = None, expires_at=None,
) -> UserRole:
    """Grant a role to a user, optionally scoped to one campus."""
    if role is None:
        if not role_code:
            raise ServiceError("Either role or role_code is required.")
        role = roles_available_to(user.organization_id).filter(code=role_code).first()
        if role is None:
            raise ServiceError(f"Unknown role '{role_code}'.", code="unknown_role")

    ensure_can_grant(granted_by, role, campus)

    # Checked before full_clean() so a repeat grant reads as a conflict rather
    # than a generic constraint-violation validation error.
    if UserRole.objects.filter(user=user, role=role, campus=campus).exists():
        raise ConflictError("This role is already assigned to the user.")

    assignment = UserRole(
        user=user,
        role=role,
        campus=campus,
        granted_by=granted_by,
        expires_at=expires_at,
    )
    assignment.full_clean()
    assignment.save()
    clear_permission_cache(user)

    log(
        AuditLog.Action.PERMISSION_CHANGE,
        instance=user,
        module="accounts",
        actor=granted_by,
        metadata={
            "granted_role": role.code,
            "campus": campus.code if campus is not None else None,
        },
    )
    return assignment


@transaction.atomic
def revoke_role(*, assignment: UserRole, revoked_by: User | None = None) -> None:
    ensure_can_grant(revoked_by, assignment.role, assignment.campus)
    user, role_code = assignment.user, assignment.role.code
    campus_code = assignment.campus.code if assignment.campus_id else None
    assignment.delete()
    clear_permission_cache(user)

    log(
        AuditLog.Action.PERMISSION_CHANGE,
        instance=user,
        module="accounts",
        actor=revoked_by,
        metadata={"revoked_role": role_code, "campus": campus_code},
    )


@transaction.atomic
def change_password(*, user: User, new_password: str, changed_by: User | None = None) -> None:
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])

    log(
        AuditLog.Action.PASSWORD_CHANGE,
        instance=user,
        module="accounts",
        actor=changed_by or user,
        metadata={"self_service": changed_by is None or changed_by.pk == user.pk},
    )


@transaction.atomic
def deactivate_user(*, user: User, deactivated_by: User | None = None) -> User:
    """Preferred over deletion: keeps history intact and blocks sign-in."""
    user.is_active = False
    user.save(update_fields=["is_active", "updated_at"])
    log(
        AuditLog.Action.UPDATE,
        instance=user,
        module="accounts",
        actor=deactivated_by,
        changes={"is_active": {"before": True, "after": False}},
    )
    return user
