"""Careers: the school's own hiring, and a job board for students and alumni.

**Own hiring.** A **Vacancy** is an opening at a campus. Applying to it
opens an ``applications.Application`` of kind ``job``: the vacancy's
application type supplies the approval chain (screening, interview, hire)
and the history. **Candidacy** ties the application to the vacancy and
holds the résumé file and the screening score. **Interviews** are
scheduled with a panel; a **JobOffer** is made at the last step and the
candidate accepts or declines it. Approving the last step with an accepted
offer hires them: a ``StaffMember`` and an HR ``Contract`` are created
through those modules' services.

**Job board.** A **JobPosting** is an outside opening (another employer's
job or internship) for students and alumni. Alumni may post; the office
approves before it shows.
"""
from django.db import models
from django.db.models import Q

from core.common.models import OrganizationOwnedModel
from modules.hr.models import ContractKind
from modules.staff.models import StaffMember

ALIVE = Q(deleted_at__isnull=True)


class VacancyStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    FILLED = "filled", "Filled"


class Vacancy(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    application_type = models.ForeignKey("applications.ApplicationType", on_delete=models.PROTECT,
                                         related_name="+", help_text="A job form: its steps decide candidates.")
    code = models.SlugField(max_length=50)
    title = models.CharField(max_length=200)
    position = models.ForeignKey("hr.Position", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    department = models.ForeignKey("academics.Department", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    staff_type = models.CharField(max_length=20, choices=StaffMember.StaffType.choices,
                                  default=StaffMember.StaffType.TEACHING)
    contract_kind = models.CharField(max_length=20, choices=ContractKind.choices, default=ContractKind.PROBATION)
    openings = models.PositiveSmallIntegerField(default=1)
    description = models.TextField(blank=True)
    requirements = models.JSONField(default=list, blank=True, help_text="A list of requirement lines.")
    min_experience_years = models.PositiveSmallIntegerField(null=True, blank=True)
    salary_range = models.CharField(max_length=100, blank=True, help_text="As advertised, e.g. 40,000–55,000.")
    opens_on = models.DateField(null=True, blank=True, help_text="Set when it opens.")
    closes_on = models.DateField(null=True, blank=True, help_text="Last day to apply.")
    is_public = models.BooleanField(default=True, help_text="Listed on the public careers page.")
    resume_required = models.BooleanField(default=True)
    status = models.CharField(max_length=10, choices=VacancyStatus.choices, default=VacancyStatus.DRAFT,
                              db_index=True)
    hired_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "careers_vacancy"
        ordering = ["-created_at", "-pk"]
        verbose_name_plural = "vacancies"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_vacancy_code"),
        ]

    def __str__(self):
        return self.title


class Candidacy(OrganizationOwnedModel):
    """One application to one vacancy. Name and contact are copied from the
    application so lists and duplicate checks don't dig into its JSON."""

    vacancy = models.ForeignKey(Vacancy, on_delete=models.PROTECT, related_name="candidacies")
    application = models.OneToOneField("applications.Application", on_delete=models.PROTECT,
                                       related_name="candidacy")
    full_name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, db_index=True)
    phone = models.CharField(max_length=32, blank=True)
    resume = models.ForeignKey("files.StoredFile", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+")
    screening_score = models.PositiveSmallIntegerField(null=True, blank=True, help_text="0–100.")
    screening_note = models.TextField(blank=True)
    hired_staff = models.ForeignKey("staff.StaffMember", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")

    class Meta:
        db_table = "careers_candidacy"
        ordering = ["-created_at", "-pk"]
        verbose_name_plural = "candidacies"

    def __str__(self):
        return f"{self.full_name} — {self.vacancy}"


class InterviewMode(models.TextChoices):
    IN_PERSON = "in_person", "In person"
    PHONE = "phone", "Phone"
    VIDEO = "video", "Video call"


class InterviewStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    NO_SHOW = "no_show", "Candidate didn't come"


class Recommendation(models.TextChoices):
    HIRE = "hire", "Hire"
    HOLD = "hold", "Hold"
    REJECT = "reject", "Don't hire"


class Interview(OrganizationOwnedModel):
    candidacy = models.ForeignKey(Candidacy, on_delete=models.CASCADE, related_name="interviews")
    round = models.PositiveSmallIntegerField(default=1)
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    mode = models.CharField(max_length=10, choices=InterviewMode.choices, default=InterviewMode.IN_PERSON)
    location = models.CharField(max_length=255, blank=True, help_text="A room, or the call link.")
    panel = models.ManyToManyField("staff.StaffMember", related_name="+", blank=True)
    status = models.CharField(max_length=10, choices=InterviewStatus.choices, default=InterviewStatus.SCHEDULED,
                              db_index=True)
    score = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True, help_text="0–10.")
    recommendation = models.CharField(max_length=10, choices=Recommendation.choices, blank=True)
    feedback = models.TextField(blank=True)

    class Meta:
        db_table = "careers_interview"
        ordering = ["scheduled_at", "pk"]


class OfferStatus(models.TextChoices):
    MADE = "made", "Made"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    WITHDRAWN = "withdrawn", "Withdrawn"


LIVE_OFFER = [OfferStatus.MADE, OfferStatus.ACCEPTED]


class JobOffer(OrganizationOwnedModel):
    candidacy = models.ForeignKey(Candidacy, on_delete=models.PROTECT, related_name="offers")
    start_date = models.DateField()
    contract_kind = models.CharField(max_length=20, choices=ContractKind.choices)
    probation_ends_on = models.DateField(null=True, blank=True)
    contract_end_date = models.DateField(null=True, blank=True, help_text="Empty: open-ended.")
    salary_note = models.CharField(max_length=255, blank=True,
                                   help_text="What was offered; payroll sets the actual pay.")
    terms = models.TextField(blank=True)
    expires_on = models.DateField(null=True, blank=True, help_text="Last day to accept.")
    status = models.CharField(max_length=10, choices=OfferStatus.choices, default=OfferStatus.MADE, db_index=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    response_note = models.CharField(max_length=255, blank=True)
    made_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "careers_offer"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["candidacy"], condition=ALIVE & Q(status__in=LIVE_OFFER),
                                    name="uniq_live_offer"),
        ]


# ---------------------------------------------------------------------------
# Job board
# ---------------------------------------------------------------------------
class PostingKind(models.TextChoices):
    FULL_TIME = "full_time", "Full-time job"
    PART_TIME = "part_time", "Part-time job"
    INTERNSHIP = "internship", "Internship"
    CONTRACT = "contract", "Contract"
    VOLUNTEER = "volunteer", "Volunteer"


class Audience(models.TextChoices):
    STUDENTS = "students", "Students"
    ALUMNI = "alumni", "Alumni"
    BOTH = "both", "Students and alumni"


class PostingStatus(models.TextChoices):
    PENDING = "pending", "Waiting for approval"
    APPROVED = "approved", "Listed"
    REJECTED = "rejected", "Not approved"
    CLOSED = "closed", "Closed"


class JobPosting(OrganizationOwnedModel):
    title = models.CharField(max_length=200)
    company = models.CharField(max_length=200)
    location = models.CharField(max_length=150, blank=True)
    kind = models.CharField(max_length=12, choices=PostingKind.choices, default=PostingKind.FULL_TIME)
    description = models.TextField()
    how_to_apply = models.TextField(blank=True)
    apply_url = models.URLField(blank=True)
    contact_email = models.EmailField(blank=True)
    closes_on = models.DateField(null=True, blank=True)
    audience = models.CharField(max_length=10, choices=Audience.choices, default=Audience.BOTH)
    status = models.CharField(max_length=10, choices=PostingStatus.choices, default=PostingStatus.PENDING,
                              db_index=True)
    posted_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    reviewed_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "careers_posting"
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.title} — {self.company}"
