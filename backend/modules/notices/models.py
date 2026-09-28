"""An announcement the office publishes to a slice of the organization.

``campus`` empty means every campus — the same nullable-scope precedent
``CalendarEvent``/``Event`` already use, so a null-aware
``Q(campus__in=...) | Q(campus__isnull=True)`` filter is required wherever
this is queried (see ``selectors.visible_to``); a plain ``campus__in`` would
silently drop every org-wide notice, since SQL's ``IN`` never matches NULL.
"""
from django.db import models
from django.utils import timezone

from core.common.models import OrganizationOwnedModel


class NoticeAudience(models.TextChoices):
    ALL = "all", "Everyone"
    STUDENTS = "students", "Students"
    PARENTS = "parents", "Parents"
    STAFF = "staff", "Staff"


class Notice(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="notices")
    audience = models.CharField(max_length=10, choices=NoticeAudience.choices, default=NoticeAudience.ALL,
                                db_index=True)
    title = models.CharField(max_length=200)
    body = models.TextField()
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        db_table = "notices_notice"
        ordering = ["-published_at", "-pk"]
        indexes = [models.Index(fields=["organization", "campus", "audience"])]

    def __str__(self):
        return self.title

    @property
    def is_published(self) -> bool:
        return self.published_at is not None and self.published_at <= timezone.now()

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at < timezone.now()
