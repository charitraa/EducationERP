"""Reusable viewset behaviour: tenant scoping, audited writes, soft deletes.

These compose through ``super()``, so the ordering in ``BaseModelViewSet`` is
deliberate: audit wraps the save, the save applies tenant kwargs, and destroy
is soft by default.
"""
from rest_framework import viewsets

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

    def get_create_kwargs(self) -> dict:
        """Tenant comes from the authenticated user, never from the payload."""
        kwargs = super().get_create_kwargs()
        user = self.request.user
        if not getattr(user, "is_platform_admin", False) and user.organization_id:
            kwargs[self.organization_field + "_id"] = user.organization_id
        return kwargs


class AuditedMixin:
    """Writes an AuditLog entry for every create/update/delete on the view."""

    audit_module = None

    def _audit_module(self) -> str:
        return self.audit_module or self.get_queryset().model._meta.app_label

    def perform_create(self, serializer):
        from core.audit.services import log_create

        super().perform_create(serializer)
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
