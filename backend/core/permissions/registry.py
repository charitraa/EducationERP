"""Declarative catalogue of permissions and system roles.

Modules declare what they can do here (or call ``register_permissions`` from
their own app config), and ``manage.py sync_permissions`` writes the catalogue
to the database. Nothing else in the codebase should create Permission rows.
"""
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class PermissionSpec:
    code: str
    name: str
    description: str = ""

    @property
    def module(self) -> str:
        return self.code.partition(".")[0]

    @property
    def action(self) -> str:
        return self.code.partition(".")[2]


@dataclass(frozen=True)
class RoleSpec:
    """A system role shipped with the platform."""

    code: str
    name: str
    description: str = ""
    permissions: tuple[str, ...] = field(default_factory=tuple)
    # "*" grants every registered permission, re-evaluated on each sync so new
    # modules are picked up automatically.
    grants_all: bool = False


_PERMISSIONS: dict[str, PermissionSpec] = {}
_ROLES: dict[str, RoleSpec] = {}
# Extra permissions modules add to system roles declared elsewhere, so each
# module keeps its own grants next to its own permissions.
_ROLE_GRANTS: dict[str, set[str]] = {}


def register_permissions(specs) -> None:
    for spec in specs:
        _PERMISSIONS[spec.code] = spec


def register_roles(specs) -> None:
    for spec in specs:
        _ROLES[spec.code] = spec


def all_permissions() -> list[PermissionSpec]:
    return sorted(_PERMISSIONS.values(), key=lambda s: (s.module, s.action))


def grant_to_system_role(role_code: str, codes) -> None:
    """Add a module's permissions to a system role, e.g. ``staff``."""
    _ROLE_GRANTS.setdefault(role_code, set()).update(codes)


def all_roles() -> list[RoleSpec]:
    roles = []
    for spec in _ROLES.values():
        extra = _ROLE_GRANTS.get(spec.code)
        if extra:
            spec = replace(spec, permissions=tuple(sorted(set(spec.permissions) | extra)))
        roles.append(spec)
    return sorted(roles, key=lambda s: s.code)


def permission_codes() -> set[str]:
    return set(_PERMISSIONS)


# ---------------------------------------------------------------------------
# Phase 1 — identity foundation
# ---------------------------------------------------------------------------
def _crud(module: str, label: str, extra=()):
    actions = [
        ("view", f"View {label}"),
        ("create", f"Create {label}"),
        ("update", f"Update {label}"),
        ("delete", f"Delete {label}"),
    ]
    actions.extend(extra)
    return [PermissionSpec(f"{module}.{action}", name) for action, name in actions]


register_permissions(
    [
        # No organizations.create / .delete: onboarding and removing a tenant
        # is restricted to platform superusers (see OrganizationViewSet).
        PermissionSpec("organizations.view", "View organizations"),
        PermissionSpec("organizations.update", "Update organizations"),
        *_crud("campuses", "campuses"),
        *_crud("users", "users", extra=[("manage_roles", "Assign and revoke user roles")]),
        *_crud("roles", "roles"),
        PermissionSpec("permissions.view", "View the permission catalogue"),
        PermissionSpec("audit.view", "View audit logs"),
    ]
)

register_roles(
    [
        RoleSpec(
            code="org-admin",
            name="Organization Administrator",
            description="Full control over one organization.",
            grants_all=True,
        ),
        RoleSpec(
            code="campus-admin",
            name="Campus Administrator",
            description="Manages users and campuses within an assigned campus.",
            permissions=(
                "organizations.view",
                "campuses.view",
                "campuses.update",
                "users.view",
                "users.create",
                "users.update",
                "users.manage_roles",
                "roles.view",
                "permissions.view",
                "audit.view",
            ),
        ),
        RoleSpec(
            code="staff",
            name="Staff",
            description="Baseline read access for employees.",
            permissions=("organizations.view", "campuses.view", "users.view"),
        ),
        RoleSpec(
            code="student",
            name="Student",
            description="Baseline role for students; module access is added per phase.",
            permissions=(),
        ),
        RoleSpec(
            code="parent",
            name="Parent",
            description="Baseline role for parents/guardians.",
            permissions=(),
        ),
    ]
)
