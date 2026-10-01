"""Two permissions, the way Phase 10 splits viewing from running things:

``hostel.view``    see buildings, rooms, beds, allocations and complaints
``hostel.manage``  the warden's office: the building register, allocating
                   beds, check-in/out, room moves and handling complaints

Billing a term's hostel fees also needs ``finance.manage``. Residents need no
permission for their own bed (``allocations/me/``) or their own complaints
(``complaints/me/``); parents see their children's beds the same way.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("hostel.view", "View hostel buildings, rooms, allocations and complaints"),
        PermissionSpec("hostel.manage", "Manage hostel rooms, allocate beds and handle complaints"),
    ]
)

grant_to_system_role("campus-admin", ["hostel.view", "hostel.manage"])
