"""Permissions this module declares, and what the shipped roles get."""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("admissions.view", "View applications"),
        PermissionSpec("admissions.create", "Record applications"),
        PermissionSpec("admissions.update", "Edit pending applications and withdraw them"),
        PermissionSpec("admissions.delete", "Delete applications that did not enroll"),
        PermissionSpec("admissions.review", "Approve or reject applications"),
        PermissionSpec("admissions.enroll", "Enroll approved applicants as students"),
    ]
)

grant_to_system_role(
    "campus-admin",
    [
        "admissions.view",
        "admissions.create",
        "admissions.update",
        "admissions.review",
        "admissions.enroll",
    ],
)
