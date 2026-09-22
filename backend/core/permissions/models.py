from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from core.common.models import BaseModel, TimeStampedModel

permission_code_validator = RegexValidator(
    r"^[a-z_]+\.[a-z_]+$",
    "Permission codes look like 'module.action', e.g. 'students.view'.",
)


class Permission(TimeStampedModel):
    """A single grantable capability, e.g. ``students.view``.

    The catalogue is platform-wide and declared in code (see
    ``core/permissions/registry.py``), then synced into the database by the
    ``sync_permissions`` command. Modules register their own permissions, so
    nothing is hardcoded at the call sites.
    """

    code = models.CharField(
        max_length=100, unique=True, validators=[permission_code_validator]
    )
    module = models.CharField(max_length=50, db_index=True)
    action = models.CharField(max_length=50)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "rbac_permission"
        ordering = ["module", "action"]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        if self.code and (not self.module or not self.action):
            self.module, _, self.action = self.code.partition(".")
        super().save(*args, **kwargs)


class Role(BaseModel):
    """A named bundle of permissions.

    System roles (``organization`` is null, ``is_system`` true) ship with the
    platform and are read-only. Each organization can also define its own.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="roles",
        help_text="Null for platform-wide system roles.",
    )
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_system = models.BooleanField(
        default=False, help_text="Shipped with the platform; cannot be edited or deleted."
    )
    permissions = models.ManyToManyField(
        Permission, related_name="roles", blank=True
    )

    class Meta:
        db_table = "rbac_role"
        ordering = ["organization__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_role_code_per_organization",
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(organization__isnull=True, deleted_at__isnull=True),
                name="uniq_system_role_code",
            ),
        ]

    def __str__(self):
        scope = self.organization.code if self.organization_id else "system"
        return f"{self.name} ({scope})"


class UserRole(TimeStampedModel):
    """Assignment of a role to a user, optionally narrowed to one campus.

    A null ``campus`` means the role applies across the whole organization.
    """

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="role_assignments"
    )
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="assignments")
    campus = models.ForeignKey(
        "organizations.Campus",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="role_assignments",
        help_text="Null means the role applies organization-wide.",
    )
    granted_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="granted_role_assignments",
    )
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Optional expiry for temporary access."
    )

    class Meta:
        db_table = "rbac_user_role"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role", "campus"],
                condition=models.Q(campus__isnull=False),
                name="uniq_user_role_per_campus",
            ),
            models.UniqueConstraint(
                fields=["user", "role"],
                condition=models.Q(campus__isnull=True),
                name="uniq_user_role_organization_wide",
            ),
        ]

    def __str__(self):
        target = self.campus.name if self.campus_id else "all campuses"
        return f"{self.user.email} → {self.role.name} @ {target}"

    def clean(self):
        """Keep assignments inside one tenant.

        A user may only receive system roles or roles from their own
        organization, and only at a campus of that organization.
        """
        errors = {}
        user_org_id = self.user.organization_id if self.user_id else None

        if self.role_id and self.role.organization_id:
            if user_org_id != self.role.organization_id:
                errors["role"] = "Role belongs to a different organization than the user."

        if self.campus_id:
            if self.campus.organization_id != user_org_id:
                errors["campus"] = "Campus belongs to a different organization than the user."

        if errors:
            raise ValidationError(errors)

    @property
    def is_expired(self) -> bool:
        from django.utils import timezone

        return self.expires_at is not None and self.expires_at <= timezone.now()
