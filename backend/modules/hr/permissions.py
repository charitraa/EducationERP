"""Three permissions, split by job:

``hr.view``           see positions, contracts, profiles, documents, leave
``hr.manage``         the HR office: set all of that up, apply for leave on
                      someone's behalf, adjust balances
``hr.approve_leave``  approve or reject leave requests (a head of department
                      can hold this alone, scoped to their campus)

Applying for, seeing and cancelling one's own leave needs no permission:
being the staff member is the authorization (``/hr/leave-requests/me/``).
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("hr.view", "View contracts, employee profiles, documents and leave"),
        PermissionSpec("hr.manage", "Manage positions, contracts, profiles, documents, leave types and balances"),
        PermissionSpec("hr.approve_leave", "Approve or reject staff leave requests"),
    ]
)

grant_to_system_role("campus-admin", ["hr.view", "hr.manage", "hr.approve_leave"])
