"""The one in-app record of a business event reaching one user.

Another module never writes here directly. It calls ``notifications.services.notify()``, which creates this row and fans
the same event out through the (currently log-only) email/SMS/push adapters
under ``integrations/``. Written synchronously, no Celery, matching how
``Invoice.paid_amount``/``StaffAttendanceDay`` are kept in step elsewhere.
"""
from django.conf import settings
from django.db import models

from core.common.models import OrganizationOwnedModel


class Notification(OrganizationOwnedModel):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    event_type = models.CharField(max_length=64, db_index=True, help_text="e.g. 'finance.payment_received'.")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True, help_text="Structured payload for a client to act on.")
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications_notification"
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["recipient", "is_read"])]

    def __str__(self):
        return f"{self.recipient}: {self.title}"
