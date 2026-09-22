from django.db import models
from django.utils import timezone

from .managers import SoftDeleteManager, SoftDeleteQuerySet


class TimeStampedModel(models.Model):
    """Adds ``created_at`` / ``updated_at`` to any model."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Soft deletion for non-financial records.

    Financial and audit records must never use this — they get adjustments or
    reversals instead so history stays intact.
    """

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    objects = SoftDeleteManager()
    all_objects = SoftDeleteManager(include_deleted=True)

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def delete(self, using=None, keep_parents=False, deleted_by=None):
        """Soft delete. Use ``hard_delete()`` when a row must really go."""
        self.deleted_at = timezone.now()
        self.deleted_by = deleted_by
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at"]
                  if hasattr(self, "updated_at") else ["deleted_at", "deleted_by"])

    def hard_delete(self, using=None, keep_parents=False):
        return super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at"]
                  if hasattr(self, "updated_at") else ["deleted_at", "deleted_by"])


class BaseModel(TimeStampedModel, SoftDeleteModel):
    """Timestamps + soft deletion. The default base for business models."""

    class Meta:
        abstract = True


class OrganizationOwnedQuerySet(SoftDeleteQuerySet):
    def for_organization(self, organization):
        return self.filter(organization=organization)


class OrganizationOwnedManager(SoftDeleteManager):
    def get_queryset(self):
        qs = OrganizationOwnedQuerySet(self.model, using=self._db)
        if self.include_deleted:
            return qs
        return qs.alive()


class OrganizationOwnedModel(BaseModel):
    """Every tenant-scoped row carries its ``organization``.

    This is what makes the platform multi-tenant without a database per
    institution. Queries must always be narrowed by organization — see
    ``core.common.mixins.OrganizationScopedMixin``.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="%(class)ss",
        db_index=True,
    )

    objects = OrganizationOwnedManager()
    all_objects = OrganizationOwnedManager(include_deleted=True)

    class Meta:
        abstract = True
