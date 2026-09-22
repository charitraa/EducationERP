from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from core.common.models import SoftDeleteModel, TimeStampedModel

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel, SoftDeleteModel):
    """The single identity for everyone on the platform.

    Students, parents, teachers and staff all authenticate through this model.
    Their role-specific data lives in profile models added in later phases
    (StudentProfile, StaffProfile, ...), each pointing back at one User — never
    a second login system.
    """

    class Type(models.TextChoices):
        STUDENT = "student", "Student"
        PARENT = "parent", "Parent"
        TEACHER = "teacher", "Teacher"
        STAFF = "staff", "Staff"
        ACCOUNTANT = "accountant", "Accountant"
        HR = "hr", "HR"
        LIBRARIAN = "librarian", "Librarian"
        ADMINISTRATOR = "administrator", "Administrator"

    email = models.EmailField(unique=True, db_index=True)
    phone = models.CharField(max_length=32, blank=True, db_index=True)

    first_name = models.CharField(max_length=150, blank=True)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)

    organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="users",
        help_text="Null only for platform-level superusers.",
    )
    user_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        default=Type.STAFF,
        db_index=True,
        help_text="What this person is. Access rights come from roles, not this field.",
    )

    is_active = models.BooleanField(default=True, db_index=True)
    is_staff = models.BooleanField(
        default=False, help_text="Can sign in to the Django admin site."
    )
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    all_objects = UserManager(include_deleted=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "auth_user_account"
        ordering = ["email"]
        verbose_name = "user"
        verbose_name_plural = "users"
        indexes = [models.Index(fields=["organization", "user_type"])]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.lower().strip()
        super().save(*args, **kwargs)

    # -- names ---------------------------------------------------------
    def get_full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p).strip() or self.email

    def get_short_name(self) -> str:
        return self.first_name or self.email

    @property
    def full_name(self) -> str:
        return self.get_full_name()

    @property
    def is_platform_admin(self) -> bool:
        """Superusers operate across every tenant; nobody else does."""
        return self.is_superuser and self.organization_id is None

    # -- permissions ---------------------------------------------------
    def get_permission_codes(self, campus=None) -> set[str]:
        """Every permission code this user holds, via their role assignments.

        Delegates to the permissions app so the RBAC rules live in one place.
        """
        from core.permissions.selectors import get_user_permission_codes

        return get_user_permission_codes(self, campus=campus)

    def has_permission(self, code: str, campus=None) -> bool:
        from core.permissions.selectors import user_has_permission

        return user_has_permission(self, code, campus=campus)
