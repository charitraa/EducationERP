"""``support.manage`` is the office: see every ticket in scope, assign,
resolve and close any of them. Anyone may raise a ticket, see their own (or
one assigned to them), and comment on a ticket they can see — no permission
code needed for that, it's checked against the ticket itself.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions([PermissionSpec("support.manage", "See, assign, resolve and close support tickets")])

grant_to_system_role("campus-admin", ["support.manage"])
