"""Permissions this module declares, and what the shipped roles get.

Timetables are run per campus, like sections and teaching assignments, so
campus-admin manages its own campus's bell schedules and weekly timetable.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("timetable.view", "View bell schedules and timetables"),
        PermissionSpec("timetable.manage", "Manage bell schedules, periods and the weekly timetable"),
    ]
)

grant_to_system_role("campus-admin", ["timetable.view", "timetable.manage"])
grant_to_system_role("staff", ["timetable.view"])
