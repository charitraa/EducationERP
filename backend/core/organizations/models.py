from django.core.validators import RegexValidator
from django.db import models

from core.common.models import BaseModel

code_validator = RegexValidator(
    r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
    "Code may contain lowercase letters, digits and hyphens only.",
)


class Organization(BaseModel):
    """A tenant: one school, college or university on the platform.

    Every tenant-scoped row in the system points back here. The platform runs
    shared-database multi-tenancy; a large customer can be split onto its own
    database later without model changes.
    """

    class Type(models.TextChoices):
        SCHOOL = "school", "School"
        COLLEGE = "college", "College"
        UNIVERSITY = "university", "University"
        INSTITUTE = "institute", "Institute"
        OTHER = "other", "Other"

    name = models.CharField(max_length=200)
    code = models.SlugField(
        max_length=50,
        unique=True,
        validators=[code_validator],
        help_text="Short immutable identifier, e.g. 'central-college'.",
    )
    legal_name = models.CharField(max_length=255, blank=True)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.COLLEGE)

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    website = models.URLField(blank=True)
    address = models.TextField(blank=True)
    timezone = models.CharField(max_length=64, default="UTC")

    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "org_organization"
        ordering = ["name"]
        verbose_name = "organization"
        verbose_name_plural = "organizations"

    def __str__(self):
        return self.name


class Campus(BaseModel):
    """A physical location belonging to an organization.

    Roles can be scoped to a campus, which is how a user ends up with
    permissions at one branch but not another.
    """

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="campuses"
    )
    name = models.CharField(max_length=200)
    code = models.SlugField(max_length=50, validators=[code_validator])

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)

    is_main = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "org_campus"
        ordering = ["organization__name", "name"]
        verbose_name = "campus"
        verbose_name_plural = "campuses"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                condition=models.Q(deleted_at__isnull=True),
                name="uniq_campus_code_per_organization",
            ),
        ]

    def __str__(self):
        return f"{self.organization.name} — {self.name}"
