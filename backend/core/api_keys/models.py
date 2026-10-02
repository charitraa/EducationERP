"""API keys: a program's own identity in an organization.

A key is backed by its own **integration user**, which cannot log in (no
usable password) and never appears in the user list. Roles are assigned to
that user exactly as to a person, optionally per campus, so every existing
permission check, campus scope and audit entry works unchanged, and the
"no granting more than you hold" rule applies to keys too. On top of its
roles a key can be **read-only**, can **expire**, can be limited to
**addresses**, and has its own **rate limit**.

The secret is shown once. Only its SHA-256 is stored: it is 256 random
bits, so a slow hash adds nothing. The short public ``prefix`` finds the
row.
"""
from django.db import models

from core.common.models import OrganizationOwnedModel


class ApiKey(OrganizationOwnedModel):
    user = models.OneToOneField("accounts.User", on_delete=models.PROTECT, related_name="api_key")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    prefix = models.CharField(max_length=16, unique=True)
    secret_hash = models.CharField(max_length=64)
    read_only = models.BooleanField(default=False, help_text="Refuse every request that changes data.")
    expires_at = models.DateTimeField(null=True, blank=True)
    allowed_ips = models.JSONField(default=list, blank=True,
                                   help_text="Addresses or networks (CIDR) it may be used from; empty: anywhere.")
    rate_limit = models.CharField(max_length=20, blank=True, help_text='e.g. "1000/hour"; empty: the default.')
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_used_ip = models.GenericIPAddressField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    revoked_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "api_keys_key"
        ordering = ["name", "pk"]

    def __str__(self):
        return f"{self.name} ({self.prefix})"
