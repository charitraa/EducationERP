"""Permissions this module declares, and what the shipped roles get."""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("staff.view", "View staff"),
        PermissionSpec("staff.create", "Create staff records"),
        PermissionSpec("staff.update", "Update staff records"),
        PermissionSpec("staff.delete", "Delete staff records"),
    ]
)

grant_to_system_role("campus-admin", ["staff.view", "staff.create", "staff.update"])
grant_to_system_role("staff", ["staff.view"])
