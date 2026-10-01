"""Two permissions, as in the hostel:

``transport.view``    see the fleet, routes, riders, trips and boarding marks
``transport.manage``  the transport office: vehicles and papers, crew, routes
                      and stops, putting riders on routes, maintenance and
                      fuel, and correcting any trip

A route's own crew (its driver and assistant) open, mark and complete that
route's trips without a permission, the way a teacher marks their own class.
Billing a term's transport fees also needs ``finance.manage``. Riders and
parents see their own routes and boarding history through ``me`` endpoints.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("transport.view", "View vehicles, routes, riders and trips"),
        PermissionSpec("transport.manage", "Manage vehicles, crew, routes, riders and trips"),
    ]
)

grant_to_system_role("campus-admin", ["transport.view", "transport.manage"])
