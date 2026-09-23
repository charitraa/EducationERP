from django.conf import settings
from django.db import models
from django.db.models import Q

from core.common.models import OrganizationOwnedModel, TimeStampedModel


class Parent(OrganizationOwnedModel):
    """A parent or guardian: anyone responsible for one or more students.

    Organization-level rather than campus-level — one family can have
    children at different campuses.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="parent_profile",
        help_text="Login account, if the parent has portal access.",
    )
    first_name = models.CharField(max_length=150)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=32, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    occupation = models.CharField(max_length=150, blank=True)
    address = models.TextField(blank=True)

    class Meta:
        db_table = "parents_parent"
        ordering = ["first_name", "last_name", "pk"]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)


class StudentParent(TimeStampedModel):
    """Links a parent to a student, and says how they are related.

    A plain link, removed outright when it ends — the audit log keeps the
    record of who was linked and when.
    """

    class Relationship(models.TextChoices):
        FATHER = "father", "Father"
        MOTHER = "mother", "Mother"
        GUARDIAN = "guardian", "Guardian"
        OTHER = "other", "Other"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="student_parents"
    )
    student = models.ForeignKey(
        "students.Student", on_delete=models.CASCADE, related_name="parent_links"
    )
    parent = models.ForeignKey(Parent, on_delete=models.CASCADE, related_name="student_links")
    relationship = models.CharField(max_length=20, choices=Relationship.choices)
    is_primary_contact = models.BooleanField(
        default=False, help_text="The first person contacted about this student."
    )

    class Meta:
        db_table = "parents_student_parent"
        ordering = ["-is_primary_contact", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["student", "parent"], name="uniq_student_parent"),
            models.UniqueConstraint(
                fields=["student"],
                condition=Q(is_primary_contact=True),
                name="uniq_primary_contact_per_student",
            ),
        ]

    def __str__(self):
        return f"{self.parent} → {self.student} ({self.relationship})"
