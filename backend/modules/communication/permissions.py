"""Two permission codes, like ``events.coordinate``/``events.manage``:

``communication.publish_slots`` is any staff member's own availability —
granted broadly, checked against ``StaffMember`` ownership for anything
beyond publishing a slot (a teacher may only approve/cancel/complete their
*own* slot's bookings this way, not anyone else's).

``communication.manage_slots`` is the office override: approve, cancel or
complete *any* booking at a campus, regardless of whose slot it is. Starting
a message thread needs no permission code — any user with a staff profile
may (checked against ``StaffMember`` directly); replying, booking an open
slot, or cancelling your own booking needs only to be signed in.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("communication.publish_slots", "Publish and edit your own appointment slots"),
        PermissionSpec("communication.manage_slots",
                       "Approve, cancel or complete any appointment booking at your campus"),
    ]
)

grant_to_system_role("campus-admin", ["communication.publish_slots", "communication.manage_slots"])
grant_to_system_role("staff", ["communication.publish_slots"])
