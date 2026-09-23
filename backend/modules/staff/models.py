from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.common.choices import Gender
from core.common.models import OrganizationOwnedModel


class StaffMember(OrganizationOwnedModel):
    """Someone who works for the institution: teachers and everyone else.

    The staff directory. Job titles live in ``designation`` as free text, so
    each institution uses its own ("HOD", "Lab Assistant", "Driver") without a
    schema change. Contracts, departments and payroll arrive with HR (Phase 11),
    which extends this record rather than replacing it.
    """

    class StaffType(models.TextChoices):
        TEACHING = "teaching", "Teaching"
        NON_TEACHING = "non_teaching", "Non-teaching"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ON_LEAVE = "on_leave", "On leave"
        LEFT = "left", "Left"

    campus = models.ForeignKey(
        "organizations.Campus",
        on_delete=models.PROTECT,
        related_name="staff_members",
        help_text="Main place of work.",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff_profile",
        help_text="Login account, if the staff member uses the system.",
    )

    employee_number = models.CharField(max_length=32)
    first_name = models.CharField(max_length=150)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=Gender.choices, blank=True)

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)

    staff_type = models.CharField(
        max_length=20, choices=StaffType.choices, default=StaffType.TEACHING, db_index=True
    )
    designation = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    joined_on = models.DateField(null=True, blank=True)
    left_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "staff_member"
        ordering = ["first_name", "last_name", "pk"]
        verbose_name = "staff member"
        indexes = [models.Index(fields=["organization", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "employee_number"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_employee_number_per_organization",
            ),
            # "Left" and a leaving date go together.
            models.CheckConstraint(
                condition=(
                    Q(status="left", left_on__isnull=False)
                    | (~Q(status="left") & Q(left_on__isnull=True))
                ),
                name="staff_left_on_matches_status",
            ),
            models.CheckConstraint(
                condition=Q(left_on__isnull=True)
                | Q(joined_on__isnull=True)
                | Q(left_on__gte=F("joined_on")),
                name="staff_leaves_after_joining",
            ),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.employee_number})"

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)
