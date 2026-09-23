"""Permissions this module declares, and what the shipped roles get.

Loaded from ``StudentsConfig.ready``; ``manage.py sync_permissions`` writes
them to the database.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("students.view", "View students"),
        PermissionSpec("students.create", "Create students"),
        PermissionSpec("students.update", "Update student details"),
        PermissionSpec("students.delete", "Delete students"),
        PermissionSpec(
            "students.change_status",
            "Transfer, suspend, reactivate, graduate or withdraw students",
        ),
    ]
)

grant_to_system_role(
    "campus-admin",
    ["students.view", "students.create", "students.update", "students.change_status"],
)
grant_to_system_role("staff", ["students.view"])
