"""Three permissions. Alumni themselves need none: their own profile,
history, RSVPs, mentoring and gifts are reached through ``me`` endpoints.

``alumni.view``       see alumni at a campus, their events, mentoring and gifts
``alumni.manage``     keep alumni records, graduate classes, run events and
                      campaigns
``alumni.donations``  record donations and refunds
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("alumni.view", "View alumni, their events, mentoring and donations"),
        PermissionSpec("alumni.manage", "Keep alumni records; graduate classes; run events and campaigns"),
        PermissionSpec("alumni.donations", "Record donations and refunds"),
    ]
)

grant_to_system_role("campus-admin", ["alumni.view", "alumni.manage", "alumni.donations"])
