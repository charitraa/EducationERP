"""Three permissions. Deciding a step needs none of these — it needs the
permission the step names (e.g. ``hostel.manage``), so each office decides
the steps that are theirs:

``applications.view``     see every application and certificate at a campus
``applications.manage``   set up forms and their approval chains; apply or
                          withdraw on someone's behalf
``applications.certify``  issue and revoke certificates (and decide the last
                          step of a certificate application)

Applicants need nothing: they submit, follow and withdraw their own (or
their children's) through ``me`` endpoints. Admission forms marked public
take submissions with no account at all.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("applications.view", "View applications and certificates"),
        PermissionSpec("applications.manage", "Set up application forms and approval chains"),
        PermissionSpec("applications.certify", "Issue and revoke certificates"),
    ]
)

grant_to_system_role("campus-admin", ["applications.view", "applications.manage", "applications.certify"])
