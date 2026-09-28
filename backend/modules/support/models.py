"""A support ticket anyone in the organization can raise: student, parent,
staff or teacher. ``campus`` follows the raiser when it can be resolved
(their own, or the campus of the child a parent names) and is left empty
otherwise — an org-wide issue, or a raiser with no resolvable campus —
matching the nullable-campus precedent ``Event``/``Notice`` already use.
"""
from django.db import models

from core.common.models import OrganizationOwnedModel, TimeStampedModel


class TicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In progress"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class SupportTicket(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="support_tickets")
    raised_by = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="raised_tickets")
    subject = models.CharField(max_length=200)
    description = models.TextField()
    status = models.CharField(max_length=12, choices=TicketStatus.choices, default=TicketStatus.OPEN, db_index=True)
    assigned_to = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="assigned_tickets")
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "support_ticket"
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["organization", "campus", "status"])]

    def __str__(self):
        return f"#{self.pk} {self.subject}"


class TicketComment(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="+")
    body = models.TextField()

    class Meta:
        db_table = "support_ticket_comment"
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.author} on #{self.ticket_id}"
