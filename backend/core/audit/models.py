from django.db import models


class AuditLog(models.Model):
    """An append-only record of who changed what, when and from where.

    Deliberately not a ``BaseModel``: audit rows are never updated and never
    soft-deleted. Financial and grade corrections must be new entries, not
    edits to history.
    """

    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        LOGIN = "login", "Login"
        LOGIN_FAILED = "login_failed", "Login failed"
        LOGOUT = "logout", "Logout"
        PASSWORD_CHANGE = "password_change", "Password change"
        PERMISSION_CHANGE = "permission_change", "Permission change"
        EXPORT = "export", "Export"

    actor = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        help_text="Null for anonymous or system actions.",
    )
    actor_email = models.EmailField(
        blank=True, help_text="Captured at write time so the trail survives user deletion."
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )

    action = models.CharField(max_length=32, choices=Action.choices, db_index=True)
    module = models.CharField(max_length=50, db_index=True)

    object_type = models.CharField(max_length=100, blank=True, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_repr = models.CharField(max_length=255, blank=True)

    changes = models.JSONField(
        default=dict,
        blank=True,
        help_text="{'field': {'before': ..., 'after': ...}} for updates.",
    )
    metadata = models.JSONField(default=dict, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    request_path = models.CharField(max_length=512, blank=True)
    request_method = models.CharField(max_length=10, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_log"
        ordering = ["-created_at"]
        verbose_name = "audit log"
        verbose_name_plural = "audit logs"
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["object_type", "object_id"]),
            models.Index(fields=["actor", "-created_at"]),
        ]

    def __str__(self):
        who = self.actor_email or "system"
        return f"{who} {self.action} {self.object_type}#{self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Audit log entries are immutable.")
        return super().save(*args, **kwargs)
