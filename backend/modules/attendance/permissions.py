"""Permissions this module declares, and what the shipped roles get.

``attendance.mark`` lets a teacher take attendance for their own classes
only: the service checks they're the day's teacher (or the class teacher).
``attendance.manage`` is the office: any class at the campus, corrections,
and staff attendance.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("attendance.view", "View attendance, registers and reports"),
        PermissionSpec("attendance.mark", "Take attendance for your own classes"),
        PermissionSpec("attendance.manage", "Take and correct any class's attendance; manage staff attendance"),
        PermissionSpec("attendance.devices", "Manage attendance devices and biometric IDs"),
    ]
)

grant_to_system_role("campus-admin", ["attendance.view", "attendance.mark", "attendance.manage",
                                      "attendance.devices"])
grant_to_system_role("staff", ["attendance.mark"])
