"""Applications: one workflow engine for every kind of request a school
handles — admission, staff leave, scholarship, hostel, transport, event,
certificate, and anything else ("general").

An organization configures **ApplicationTypes**: a kind, optional extra
questions, and ordered **approval steps**, each naming a permission. An
**Application** moves through those steps; anyone holding the step's
permission for the applicant's campus decides it. On the final approval the
kind's handler (``kinds.py``) acts through the owning module's service — a
bed is reserved, a leave approved, a certificate issued — inside the same
transaction, so a failure (the bed was taken) refuses the approval instead
of leaving the two out of step. Every submission, decision and return is an
**ApplicationEvent**, never edited.
"""
from django.db import models
from django.db.models import Q

from core.common.models import OrganizationOwnedModel

ALIVE = Q(deleted_at__isnull=True)


class Kind(models.TextChoices):
    ADMISSION = "admission", "Admission"
    LEAVE = "leave", "Staff leave"
    SCHOLARSHIP = "scholarship", "Scholarship"
    HOSTEL = "hostel", "Hostel bed"
    TRANSPORT = "transport", "Transport"
    EVENT = "event", "Event participation"
    CERTIFICATE = "certificate", "Certificate"
    GENERAL = "general", "General request"


class ApplicationType(OrganizationOwnedModel):
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    description = models.TextField(blank=True)
    campus = models.ForeignKey("organizations.Campus", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+", help_text="Empty: open at every campus.")
    is_public = models.BooleanField(default=False,
                                    help_text="Admission only: accept submissions without an account.")
    is_active = models.BooleanField(default=True, db_index=True)
    fields = models.JSONField(default=list, blank=True,
                              help_text="Extra questions: [{name, label, type, required, choices}].")
    certificate_title = models.CharField(max_length=150, blank=True,
                                         help_text="Certificate kind: the title printed, e.g. Character certificate.")

    class Meta:
        db_table = "applications_type"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE,
                                    name="uniq_application_type_code"),
            models.CheckConstraint(condition=Q(is_public=False) | Q(kind="admission"),
                                   name="application_type_public_admission_only"),
        ]

    def __str__(self):
        return self.name


class ApprovalStep(models.Model):
    """One step of a type's chain. Replaced as a whole with the type's
    ``steps``, and only while no application of the type is open."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    application_type = models.ForeignKey(ApplicationType, on_delete=models.CASCADE, related_name="steps")
    sequence = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=100, help_text="e.g. Class teacher, Warden, Principal.")
    permission = models.CharField(max_length=100, help_text="A permission code; its holders decide this step.")

    class Meta:
        db_table = "applications_step"
        ordering = ["application_type_id", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["application_type", "sequence"], name="uniq_application_step"),
        ]

    def __str__(self):
        return f"{self.sequence}. {self.name}"


class Status(models.TextChoices):
    IN_REVIEW = "in_review", "In review"
    RETURNED = "returned", "Sent back to the applicant"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


OPEN = [Status.IN_REVIEW, Status.RETURNED]


class Application(OrganizationOwnedModel):
    """One request. ``step`` is the sequence of the step deciding it now.
    It is about a ``student`` or a ``staff`` member, or — for admission —
    about the person in ``data``; ``applicant`` is who sent it (empty for a
    public form, which is tracked by ``token_hash`` instead)."""

    application_type = models.ForeignKey(ApplicationType, on_delete=models.PROTECT, related_name="applications")
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    number = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.IN_REVIEW, db_index=True)
    step = models.PositiveSmallIntegerField(default=1)
    applicant = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    student = models.ForeignKey("students.Student", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    staff = models.ForeignKey("staff.StaffMember", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    data = models.JSONField(default=dict, blank=True)
    token_hash = models.CharField(max_length=64, blank=True, help_text="SHA-256 of a public applicant's token.")
    submitted_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    outcome = models.JSONField(default=dict, blank=True,
                               help_text="What approval created, e.g. {\"type\": \"hostel.allocation\", \"id\": 7}.")
    outcome_label = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "applications_application"
        ordering = ["-submitted_at", "-pk"]
        indexes = [models.Index(fields=["organization", "status", "campus"])]
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="uniq_application_number"),
            models.CheckConstraint(condition=~Q(student__isnull=False, staff__isnull=False),
                                   name="application_one_subject"),
        ]

    def __str__(self):
        return self.number

    @property
    def subject_name(self) -> str:
        if self.student_id:
            return self.student.full_name
        if self.staff_id:
            return self.staff.full_name
        data = self.data or {}
        name = " ".join(p for p in (data.get("first_name"), data.get("last_name")) if p)
        return name or self.contact_name


class EventAction(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Step approved"
    COMPLETED = "completed", "Approved (final)"
    REJECTED = "rejected", "Rejected"
    RETURNED = "returned", "Sent back"
    RESUBMITTED = "resubmitted", "Resubmitted"
    WITHDRAWN = "withdrawn", "Withdrawn"


class ApplicationEvent(models.Model):
    """The history. Step name and permission are copied so the trail reads
    the same after a type's steps are edited."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=12, choices=EventAction.choices)
    step = models.PositiveSmallIntegerField(null=True, blank=True)
    step_name = models.CharField(max_length=100, blank=True)
    by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.TextField(blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "applications_event"
        ordering = ["at", "pk"]


class Certificate(OrganizationOwnedModel):
    """A numbered certificate issued to a student, with the facts it states
    frozen at issue, so a later transfer or name fix doesn't rewrite it.
    Revoked, never deleted. Clients render the PDF, as with report cards."""

    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="+")
    application = models.OneToOneField(Application, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="certificate")
    title = models.CharField(max_length=150)
    number = models.CharField(max_length=30)
    purpose = models.CharField(max_length=255, blank=True)
    issued_on = models.DateField()
    contents = models.JSONField(default=dict)
    issued_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=255, blank=True)
    revoked_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        db_table = "applications_certificate"
        ordering = ["-issued_on", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="uniq_certificate_number"),
        ]

    def __str__(self):
        return f"{self.number} {self.title}"
