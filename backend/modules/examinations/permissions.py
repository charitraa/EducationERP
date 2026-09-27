"""Permissions this module declares, and what the shipped roles get.

``exams.mark`` lets a teacher enter marks for a subject they teach to the
section: the service checks the teaching assignment. ``exams.manage`` is the
exam office: setup, schedules, seating, admit cards, verifying and correcting
marks. Publishing results is its own permission, so it can sit with a
principal or exam controller rather than every clerk.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("exams.view", "View exams, schedules, mark sheets and results"),
        PermissionSpec("exams.manage", "Set up exams, seating and admit cards; verify and correct marks"),
        PermissionSpec("exams.mark", "Enter marks for the subjects you teach"),
        PermissionSpec("exams.publish", "Publish and unpublish results"),
        PermissionSpec("grades.view", "View grade scales"),
        PermissionSpec("grades.manage", "Create and change grade scales"),
    ]
)

grant_to_system_role("campus-admin", ["exams.view", "exams.manage", "exams.mark", "exams.publish",
                                      "grades.view", "grades.manage"])
grant_to_system_role("staff", ["exams.mark", "grades.view"])
