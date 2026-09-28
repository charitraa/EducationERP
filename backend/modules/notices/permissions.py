"""``notices.manage`` is the office: create, edit, publish and delete a
notice. Viewing needs no permission code — every organization member reads
notices matching their audience and campus, through ``selectors.visible_to``.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions([PermissionSpec("notices.manage", "Create, publish and delete notices")])

grant_to_system_role("campus-admin", ["notices.manage"])
