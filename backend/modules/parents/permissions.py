"""Permissions this module declares, and what the shipped roles get."""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("parents.view", "View parents"),
        PermissionSpec("parents.create", "Create parents"),
        PermissionSpec("parents.update", "Update parents and link them to students"),
        PermissionSpec("parents.delete", "Delete parents"),
    ]
)

grant_to_system_role("campus-admin", ["parents.view", "parents.create", "parents.update"])
grant_to_system_role("staff", ["parents.view"])
