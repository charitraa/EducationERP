"""Permissions this module declares, and what the shipped roles get.

Two levels of change, because they belong to different people: the
organization-wide structure (departments, programs, subjects, curriculum,
academic years, terms) is set by academic administration; the campus-level
classes (rooms, batches, sections, who teaches what) are run per campus.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("academics.view", "View the academic structure and classes"),
        PermissionSpec(
            "academics.manage_structure",
            "Manage departments, programs, subjects, curriculum, academic years and terms",
        ),
        PermissionSpec(
            "academics.manage_classes",
            "Manage rooms, batches, sections and teaching assignments",
        ),
    ]
)

grant_to_system_role("campus-admin", ["academics.view", "academics.manage_classes"])
grant_to_system_role("staff", ["academics.view"])
