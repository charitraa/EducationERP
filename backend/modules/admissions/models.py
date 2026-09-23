from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.common.choices import Gender
from core.common.models import OrganizationOwnedModel
from modules.parents.models import StudentParent


class Admission(OrganizationOwnedModel):
    """An application to study at a campus, from receipt to enrollment.

    Flow:  pending ──approve──► approved ──enroll──► enrolled
              │                    │
              ├──reject──► rejected │
              └──withdraw──────────┴──► withdrawn

    The applicant's details are kept as submitted. Enrolling copies them into
    a new Student (and the guardian into a Parent) through those modules'
    services; the application itself stays as the record of what was applied.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        ENROLLED = "enrolled", "Enrolled"

    campus = models.ForeignKey(
        "organizations.Campus", on_delete=models.PROTECT, related_name="admissions"
    )
    application_number = models.CharField(max_length=32)
    applied_on = models.DateField(default=timezone.localdate)
    applying_for = models.CharField(
        max_length=200,
        blank=True,
        help_text="Program or grade applied for. Becomes a link to a program in Phase 3.",
    )

    first_name = models.CharField(max_length=150)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=Gender.choices, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)
    previous_school = models.CharField(max_length=200, blank=True)

    guardian_first_name = models.CharField(max_length=150, blank=True)
    guardian_last_name = models.CharField(max_length=150, blank=True)
    guardian_relationship = models.CharField(
        max_length=20,
        blank=True,
        choices=StudentParent.Relationship.choices,
    )
    guardian_phone = models.CharField(max_length=32, blank=True)
    guardian_email = models.EmailField(blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    decision_note = models.TextField(blank=True)
    student = models.OneToOneField(
        "students.Student",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="admission",
        help_text="The student record created on enrollment.",
    )

    class Meta:
        db_table = "admissions_admission"
        ordering = ["-applied_on", "-pk"]
        indexes = [models.Index(fields=["organization", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "application_number"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_application_number_per_organization",
            ),
            # Enrolled exactly when a student record exists for it.
            models.CheckConstraint(
                condition=(
                    Q(status="enrolled", student__isnull=False)
                    | (~Q(status="enrolled") & Q(student__isnull=True))
                ),
                name="admission_student_matches_status",
            ),
        ]

    def __str__(self):
        return f"{self.application_number} — {self.full_name}"

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)

    @property
    def is_open(self) -> bool:
        """Still editable: nothing has been decided yet."""
        return self.status == self.Status.PENDING
