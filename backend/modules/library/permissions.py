"""``library.manage`` runs the catalog and memberships (books, authors,
categories, publishers, shelves, copies, members). ``library.circulate`` is
the front-desk job: issue, return, fines, reservations. Split the same way
the examinations module splits ``exams.manage`` from ``exams.mark`` — an institution that
wants a librarian who circulates books but doesn't touch the catalog can
grant just the one permission through a custom role.

Browsing the catalog and checking a copy's availability needs no permission
code — every organization member can (`BookViewSet`/`CopyViewSet` read
access), the same way notices and events are readable by default.
"""
from core.permissions.registry import PermissionSpec, grant_to_system_role, register_permissions

register_permissions(
    [
        PermissionSpec("library.manage", "Manage the library catalog and memberships"),
        PermissionSpec("library.circulate", "Issue, return, and settle fines and reservations"),
    ]
)

grant_to_system_role("campus-admin", ["library.manage", "library.circulate"])
