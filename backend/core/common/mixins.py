"""Reusable viewset behaviour: tenant scoping, audited writes, soft deletes.

These compose through ``super()``, so the ordering in ``BaseModelViewSet`` is
deliberate: audit wraps the save, the save applies tenant kwargs, and destroy
is soft by default.
"""
from rest_framework import serializers, viewsets
from rest_framework.exceptions import PermissionDenied

from core.common.permissions import HasPermission, IsSameOrganization


class SaveKwargsMixin:
    """Bottom of the write chain: performs the actual save.

    Other mixins contribute keyword arguments through ``get_create_kwargs``
    instead of overriding ``perform_create``, which keeps the ``super()``
    chain intact.
    """

    def get_create_kwargs(self) -> dict:
        return {}

    def get_update_kwargs(self) -> dict:
        return {}

    def perform_create(self, serializer):
        serializer.save(**self.get_create_kwargs())

    def perform_update(self, serializer):
        serializer.save(**self.get_update_kwargs())


class OrganizationScopedMixin:
    """Narrows every queryset to the caller's organization.

    Platform superusers see everything. For anyone else the tenant boundary is
    applied in the queryset rather than in view logic, so no endpoint can
    forget it. ``organization_field`` handles models that reach the tenant
    through a relation.
    """

    organization_field = "organization"

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user

        if getattr(user, "is_platform_admin", False):
            return qs
        if not user.is_authenticated or user.organization_id is None:
            return qs.none()

        return qs.filter(**{f"{self.organization_field}_id": user.organization_id})

    def get_target_organization_id(self) -> int | None:
        """The tenant a new record is created in.

        Always the caller's own organization. Platform superusers have none,
        so they name it with ``organization`` in the payload — the one case a
        tenant id is accepted from a request, because they may act in any.
        """
        user = self.request.user
        if not getattr(user, "is_platform_admin", False):
            if user.organization_id is None:
                raise PermissionDenied("Your account does not belong to an organization.")
            return user.organization_id

        from core.organizations.models import Organization

        raw = self.request.data.get("organization")
        try:
            organization_id = int(raw)
        except (TypeError, ValueError):
            organization_id = None
        if organization_id is None or not Organization.objects.filter(pk=organization_id).exists():
            raise serializers.ValidationError(
                {"organization": ["Platform administrators must name an existing organization."]}
            )
        return organization_id

    def get_create_kwargs(self) -> dict:
        """Tenant comes from the authenticated user, never from the payload."""
        kwargs = super().get_create_kwargs()
        kwargs[self.organization_field + "_id"] = self.get_target_organization_id()
        return kwargs


class CampusScopedMixin:
    """Limits campus-owned records to the campuses a role covers.

    A role granted for one campus (``UserRole.campus``) should reach that
    campus's students, staff and admissions — not the whole organization.
    The queryset keeps only rows at campuses where the caller holds the
    action's permission; writes must target such a campus too.

    Organization-wide roles and superusers see every campus. Sits in front of
    ``OrganizationScopedMixin``, which still applies the tenant boundary.
    """

    campus_field = "campus"

    def _required_codes(self) -> list[str]:
        return HasPermission().get_required_permissions(self.request, self)

    def get_queryset(self):
        from core.permissions.selectors import campus_ids_with_permission

        qs = super().get_queryset()
        user = self.request.user
        if not user.is_authenticated:
            return qs.none()

        for code in self._required_codes():
            campus_ids = campus_ids_with_permission(user, code)
            if campus_ids is not None:
                qs = qs.filter(**{f"{self.campus_field}__in": campus_ids})
        return qs

    def check_campus_allowed(self, campus) -> None:
        """Refuse writes that target a campus the caller's roles don't cover."""
        from core.permissions.selectors import campus_ids_with_permission

        if campus is None:
            return
        campus_id = getattr(campus, "pk", campus)
        for code in self._required_codes():
            campus_ids = campus_ids_with_permission(self.request.user, code)
            if campus_ids is not None and campus_id not in campus_ids:
                raise PermissionDenied(
                    "Your role does not cover this campus for this action."
                )

    def campus_of(self, validated_data):
        """The campus a write targets. Override when it comes through a
        relation, e.g. a teaching assignment's section."""
        return validated_data.get(self.campus_field)

    def perform_create(self, serializer):
        self.check_campus_allowed(self.campus_of(serializer.validated_data))
        super().perform_create(serializer)

    def perform_update(self, serializer):
        # The existing row is already in scope (get_queryset); this covers a
        # move to a different campus.
        self.check_campus_allowed(self.campus_of(serializer.validated_data))
        super().perform_update(serializer)


class AuditedMixin:
    """Writes an AuditLog entry for every create/update/delete on the view."""

    audit_module = None
    # True when the serializer creates through a service that writes its own
    # audit entry — logging here as well would record the creation twice.
    service_audits_create = False

    def _audit_module(self) -> str:
        return self.audit_module or self.get_queryset().model._meta.app_label

    def perform_create(self, serializer):
        from core.audit.services import log_create

        super().perform_create(serializer)
        if not self.service_audits_create:
            log_create(self.request, serializer.instance, module=self._audit_module())

    def perform_update(self, serializer):
        from core.audit.services import log_update, snapshot

        before = snapshot(serializer.instance)
        super().perform_update(serializer)
        log_update(
            self.request, serializer.instance, before=before, module=self._audit_module()
        )

    def perform_destroy(self, instance):
        from core.audit.services import log_delete

        log_delete(self.request, instance, module=self._audit_module())
        super().perform_destroy(instance)


class SoftDeleteMixin:
    """DELETE soft-deletes and records who did it."""

    def perform_destroy(self, instance):
        if hasattr(instance, "deleted_at"):
            instance.delete(deleted_by=self.request.user)
        else:
            instance.delete()


class BaseModelViewSet(
    AuditedMixin, SoftDeleteMixin, SaveKwargsMixin, viewsets.ModelViewSet
):
    """Audited, soft-deleting, permission-driven CRUD."""

    permission_classes = [HasPermission, IsSameOrganization]


class OrganizationScopedViewSet(OrganizationScopedMixin, BaseModelViewSet):
    """The default base for tenant-owned resources."""


class CampusScopedViewSet(CampusScopedMixin, OrganizationScopedViewSet):
    """Tenant-owned resources that also belong to one campus."""
