from django.db import models
from django.utils import timezone


class SoftDeleteQuerySet(models.QuerySet):
    """QuerySet that understands the ``deleted_at`` convention."""

    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def dead(self):
        return self.filter(deleted_at__isnull=False)

    def delete(self):
        """Soft-delete the whole queryset in a single UPDATE."""
        return self.update(deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()


class SoftDeleteManager(models.Manager):
    """Default manager: hides soft-deleted rows.

    ``Model.all_objects`` is kept alongside it for the cases where deleted
    rows still matter (audit trails, restore flows, admin screens).
    """

    def __init__(self, *args, include_deleted=False, **kwargs):
        self.include_deleted = include_deleted
        super().__init__(*args, **kwargs)

    def get_queryset(self):
        qs = SoftDeleteQuerySet(self.model, using=self._db)
        if self.include_deleted:
            return qs
        return qs.alive()
