"""Permissions this module declares, and what the shipped roles get.

``events.coordinate`` lets a staff member run the events they organize:
approve or reject registrations, take attendance and record participation
for that event only (checked against ``Event.organized_by``).
``events.manage`` is the office: every event, categories, point rules and
award rules, and granting an award by hand.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("events.view", "View events, registrations, points and awards"),
        PermissionSpec("events.coordinate", "Run the events you organize: registrations, attendance, participation"),
        PermissionSpec("events.manage", "Set up events, categories, point rules and award rules; grant awards by hand"),
    ]
)

grant_to_system_role("campus-admin", ["events.view", "events.coordinate", "events.manage"])
grant_to_system_role("staff", ["events.view", "events.coordinate"])
