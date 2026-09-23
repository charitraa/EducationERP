from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from core.common.choices import Gender
from core.common.models import OrganizationOwnedModel, TimeStampedModel


class Student(OrganizationOwnedModel):
    """A learner's record: the source of truth for who the student is.

    ``user`` is the optional login account. Young students often have none
    (their parents use the portal instead), so the student record carries its
    own legal name and details rather than borrowing them from an account.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        GRADUATED = "graduated", "Graduated"
        WITHDRAWN = "withdrawn", "Withdrawn"

    campus = models.ForeignKey(
        "organizations.Campus",
        on_delete=models.PROTECT,
        related_name="students",
        help_text="Where the student currently studies. Changed only by a transfer.",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="student_profile",
        help_text="Login account, if the student has portal access.",
    )

    student_number = models.CharField(
        max_length=32, help_text="Admission/registration number, unique per organization."
    )
    first_name = models.CharField(max_length=150)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=Gender.choices, blank=True)

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    admitted_on = models.DateField(default=timezone.localdate)

    class Meta:
        db_table = "students_student"
        ordering = ["first_name", "last_name", "pk"]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "campus"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "student_number"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_student_number_per_organization",
            ),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.student_number})"

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)

    @property
    def is_enrolled(self) -> bool:
        return self.status in (self.Status.ACTIVE, self.Status.SUSPENDED)


class Enrollment(TimeStampedModel):
    """One continuous period of a student's study at a campus.

    History, not state: rows are closed, never edited away or deleted, so a
    student's path (admitted, placed in Grade 5 A, promoted to Grade 6 B,
    transferred, graduated) can always be rebuilt.

    ``section`` is the academic placement. It carries the year, program,
    level and batch, so none of them are copied here. A new enrollment
    starts unplaced; placing it the first time fills ``section`` in, and
    every later move closes the row as ``moved`` and opens a new one.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        TRANSFERRED = "transferred", "Transferred"
        WITHDRAWN = "withdrawn", "Withdrawn"
        MOVED = "moved", "Moved to another section"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="enrollments"
    )
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="enrollments")
    campus = models.ForeignKey(
        "organizations.Campus", on_delete=models.PROTECT, related_name="enrollments"
    )
    section = models.ForeignKey(
        "academics.Section",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="enrollments",
        help_text="Academic placement. Empty until the student is placed.",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    started_on = models.DateField()
    ended_on = models.DateField(null=True, blank=True)
    end_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "students_enrollment"
        ordering = ["-started_on", "-pk"]
        constraints = [
            # At most one open enrollment per student.
            models.UniqueConstraint(
                fields=["student"],
                condition=Q(status="active"),
                name="uniq_active_enrollment_per_student",
            ),
            # Open means no end date; closed means one.
            models.CheckConstraint(
                condition=(
                    Q(status="active", ended_on__isnull=True)
                    | (~Q(status="active") & Q(ended_on__isnull=False))
                ),
                name="enrollment_end_date_matches_status",
            ),
            models.CheckConstraint(
                condition=Q(ended_on__isnull=True) | Q(ended_on__gte=F("started_on")),
                name="enrollment_ends_after_it_starts",
            ),
        ]

    def __str__(self):
        return f"{self.student} @ {self.campus.name} ({self.status})"
