"""Public signup: someone creates their own organization.

Nothing is created until the email address is proven: a signup is only a
**request** holding what was typed (the password already hashed) and a
single-use link token (only its SHA-256 is kept). Following the link
creates the organization, its main campus and the person as its org-admin
— or, with ``SIGNUP_REQUIRE_APPROVAL``, queues it for a platform admin.
Unverified requests never reserve a code or an email, so nobody can squat
on them by signing up without verifying.
"""
from django.db import models

from core.common.models import TimeStampedModel
from core.organizations.models import Organization, code_validator


class SignupRequest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for email verification"
        AWAITING_APPROVAL = "awaiting_approval", "Verified, waiting for approval"
        COMPLETED = "completed", "Organization created"
        REJECTED = "rejected", "Rejected"

    organization_name = models.CharField(max_length=200)
    organization_code = models.CharField(max_length=50, validators=[code_validator], db_index=True)
    organization_type = models.CharField(max_length=20, choices=Organization.Type.choices,
                                         default=Organization.Type.COLLEGE)
    timezone = models.CharField(max_length=64, default="UTC")
    admin_email = models.EmailField(db_index=True)
    admin_first_name = models.CharField(max_length=150)
    admin_last_name = models.CharField(max_length=150, blank=True)
    admin_phone = models.CharField(max_length=32, blank=True)
    password_hash = models.CharField(max_length=128)
    token_hash = models.CharField(max_length=64, blank=True, db_index=True)
    token_expires_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    rejection_reason = models.CharField(max_length=500, blank=True)
    organization = models.ForeignKey("organizations.Organization", null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "signup_request"
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.organization_name} ({self.admin_email}, {self.status})"
