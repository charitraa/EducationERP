"""DRF permission classes driving the ``User -> Role -> Permission`` model.

Views declare what they need; they never test for role names or user types.

    class CampusViewSet(OrganizationScopedViewSet):
        required_permissions = {
            "list": ["campuses.view"],
            "create": ["campuses.create"],
        }
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

# Fallback mapping when a view lists permissions for a resource rather than
# per action, e.g. ``permission_resource = "campuses"``.
_ACTION_TO_VERB = {
    "list": "view",
    "retrieve": "view",
    "create": "create",
    "update": "update",
    "partial_update": "update",
    "destroy": "delete",
}


class HasPermission(BasePermission):
    """Checks the view's declared permission codes against the user's roles."""

    message = "You do not have permission to perform this action."

    def get_required_permissions(self, request, view) -> list[str]:
        action = getattr(view, "action", None) or _method_to_action(request.method)

        declared = getattr(view, "required_permissions", None)
        if isinstance(declared, dict):
            if action in declared:
                return list(declared[action])
            if "default" in declared:
                return list(declared["default"])
            # An action with no declared requirement is closed, not open.
            return []
        if declared:
            return list(declared)

        resource = getattr(view, "permission_resource", None)
        if resource:
            verb = _ACTION_TO_VERB.get(action)
            if verb:
                return [f"{resource}.{verb}"]
        return []

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if user.is_superuser:
            return True

        required = self.get_required_permissions(request, view)
        if not required:
            # Nothing declared: deny writes, allow reads only if the view says so.
            return bool(getattr(view, "allow_undeclared_actions", False))

        campus = getattr(request, "campus", None)
        held = user.get_permission_codes(campus=campus)
        return all(code in held for code in required)


class ApiDocsAccess(BasePermission):
    """The schema and docs pages: open when ``API_DOCS_PUBLIC`` is on,
    otherwise staff only. A published schema is a map of every endpoint, so
    production keeps it to people who work on the system."""

    def has_permission(self, request, view):
        from django.conf import settings

        if settings.API_DOCS_PUBLIC:
            return True
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff)


class IsPlatformAdmin(BasePermission):
    """Only platform superusers (no organization of their own)."""

    message = "This action is restricted to platform administrators."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_platform_admin)


class IsSameOrganization(BasePermission):
    """Object-level guard: the object must belong to the caller's tenant.

    Defence in depth — querysets are already organization-scoped, but detail
    routes get checked again so a stray queryset can't leak across tenants.
    """

    message = "This object belongs to a different organization."

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_platform_admin:
            return True

        obj_org_id = _organization_id_of(obj)
        if obj_org_id is None:
            return True
        return obj_org_id == user.organization_id


class ReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS


def _method_to_action(method: str) -> str:
    return {
        "GET": "list",
        "HEAD": "list",
        "OPTIONS": "list",
        "POST": "create",
        "PUT": "update",
        "PATCH": "partial_update",
        "DELETE": "destroy",
    }.get(method.upper(), "list")


def _organization_id_of(obj):
    """Find the tenant an object belongs to, however it is attached."""
    from core.organizations.models import Organization

    if isinstance(obj, Organization):
        return obj.pk
    return getattr(obj, "organization_id", None)
