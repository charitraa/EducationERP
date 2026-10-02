"""Alumni: the people who graduated, what they do now, and how they stay in
touch: events, mentoring current students, and giving.

An **AlumniProfile** is made automatically when a student graduates
(``students.signals.student_graduated``), copying who they were and what
they finished. The office can also add alumni from before the system. A
graduate keeps their login (``user_type`` becomes ``alumni``) and keeps
their own profile, jobs, studies and achievements up to date.

Money follows the finance rule: a **Donation** is never edited; a refund
is a new **DonationRefund** row, and the totals on the donation and its
campaign are kept in step under a row lock.
"""
from decimal import Decimal

from django.db import models
from django.db.models import Q

from core.common.choices import Gender
from core.common.models import OrganizationOwnedModel
from modules.finance.models import PaymentMethod

ALIVE = Q(deleted_at__isnull=True)
ZERO = Decimal("0.00")


class AlumniProfile(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+",
                               help_text="Where they studied.")
    student = models.OneToOneField("students.Student", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="alumni_profile",
                                   help_text="Empty for alumni from before the system.")
    user = models.OneToOneField("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="alumni_profile")
    first_name = models.CharField(max_length=150)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150)
    gender = models.CharField(max_length=20, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)

    # Graduation: copied when they graduate, so a later rename of the
    # program or year doesn't rewrite what they finished.
    program = models.ForeignKey("academics.Program", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    program_name = models.CharField(max_length=150, blank=True)
    level = models.PositiveSmallIntegerField(null=True, blank=True)
    section_name = models.CharField(max_length=100, blank=True)
    academic_year = models.CharField(max_length=50, blank=True, help_text="e.g. 2082/83.")
    graduated_on = models.DateField(null=True, blank=True)

    linkedin_url = models.URLField(blank=True)
    website_url = models.URLField(blank=True)
    bio = models.TextField(blank=True)
    directory_visible = models.BooleanField(default=False,
                                            help_text="Listed in the alumni directory for other alumni.")
    is_mentor = models.BooleanField(default=False, db_index=True)
    mentor_topics = models.CharField(max_length=255, blank=True)
    mentor_capacity = models.PositiveSmallIntegerField(default=3,
                                                       help_text="Mentees at once.")

    class Meta:
        db_table = "alumni_profile"
        ordering = ["last_name", "first_name", "pk"]
        indexes = [models.Index(fields=["organization", "academic_year"])]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.middle_name, self.last_name) if p)


class Employment(OrganizationOwnedModel):
    profile = models.ForeignKey(AlumniProfile, on_delete=models.CASCADE, related_name="employments")
    employer = models.CharField(max_length=200)
    title = models.CharField(max_length=150)
    location = models.CharField(max_length=150, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Empty: works there now.")

    class Meta:
        db_table = "alumni_employment"
        ordering = ["profile_id", "-start_date", "-pk"]


class StudyStatus(models.TextChoices):
    ONGOING = "ongoing", "Ongoing"
    COMPLETED = "completed", "Completed"
    DISCONTINUED = "discontinued", "Discontinued"


class HigherStudy(OrganizationOwnedModel):
    profile = models.ForeignKey(AlumniProfile, on_delete=models.CASCADE, related_name="studies")
    institution = models.CharField(max_length=200)
    qualification = models.CharField(max_length=150, help_text="e.g. BSc, MBA, A levels.")
    field = models.CharField(max_length=150, blank=True)
    country = models.CharField(max_length=100, blank=True)
    start_year = models.PositiveSmallIntegerField()
    end_year = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=StudyStatus.choices, default=StudyStatus.ONGOING)

    class Meta:
        db_table = "alumni_higher_study"
        ordering = ["profile_id", "-start_year", "-pk"]


class Achievement(OrganizationOwnedModel):
    profile = models.ForeignKey(AlumniProfile, on_delete=models.CASCADE, related_name="achievements")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    achieved_on = models.DateField(null=True, blank=True)
    url = models.URLField(blank=True)

    class Meta:
        db_table = "alumni_achievement"
        ordering = ["profile_id", "-achieved_on", "-pk"]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
class EventStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    CANCELLED = "cancelled", "Cancelled"


class AlumniEvent(OrganizationOwnedModel):
    """A reunion, meetup or talk for alumni. Its own small model: Phase 7's
    events are for enrolled students and their attendance and points."""

    campus = models.ForeignKey("organizations.Campus", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+", help_text="Empty: alumni of every campus.")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    venue = models.CharField(max_length=255, blank=True)
    online_url = models.URLField(blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True, help_text="Places, guests included.")
    status = models.CharField(max_length=10, choices=EventStatus.choices, default=EventStatus.DRAFT,
                              db_index=True)

    class Meta:
        db_table = "alumni_event"
        ordering = ["-starts_at", "-pk"]

    def __str__(self):
        return self.title


class RsvpResponse(models.TextChoices):
    GOING = "going", "Going"
    MAYBE = "maybe", "Maybe"
    DECLINED = "declined", "Not going"


class Rsvp(OrganizationOwnedModel):
    event = models.ForeignKey(AlumniEvent, on_delete=models.CASCADE, related_name="rsvps")
    profile = models.ForeignKey(AlumniProfile, on_delete=models.CASCADE, related_name="rsvps")
    response = models.CharField(max_length=10, choices=RsvpResponse.choices)
    guests = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "alumni_rsvp"
        ordering = ["event_id", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["event", "profile"], condition=ALIVE, name="uniq_alumni_rsvp"),
        ]


# ---------------------------------------------------------------------------
# Mentoring
# ---------------------------------------------------------------------------
class MentorshipStatus(models.TextChoices):
    PENDING = "pending", "Requested"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Cancelled"
    ENDED = "ended", "Ended"


LIVE_MENTORSHIP = [MentorshipStatus.PENDING, MentorshipStatus.ACCEPTED]


class Mentorship(OrganizationOwnedModel):
    """A student, or a younger graduate, asks an alumni mentor for help."""

    mentor = models.ForeignKey(AlumniProfile, on_delete=models.PROTECT, related_name="mentees")
    student = models.ForeignKey("students.Student", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    mentee = models.ForeignKey(AlumniProfile, null=True, blank=True, on_delete=models.PROTECT,
                               related_name="mentors", help_text="When the mentee is a graduate.")
    topic = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=MentorshipStatus.choices, default=MentorshipStatus.PENDING,
                              db_index=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "alumni_mentorship"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=(Q(student__isnull=False) & Q(mentee__isnull=True))
                                   | (Q(student__isnull=True) & Q(mentee__isnull=False)),
                                   name="alumni_mentorship_one_mentee"),
        ]


# ---------------------------------------------------------------------------
# Giving
# ---------------------------------------------------------------------------
class Campaign(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="+", help_text="Empty: for the whole organization.")
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    goal_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    raised_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO,
                                        help_text="Donations less refunds; kept by the service.")

    class Meta:
        db_table = "alumni_campaign"
        ordering = ["-starts_on", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_alumni_campaign"),
        ]

    def __str__(self):
        return self.name


class Donation(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+",
                               help_text="The office that received it.")
    campaign = models.ForeignKey(Campaign, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="donations", help_text="Empty: a general gift.")
    donor = models.ForeignKey(AlumniProfile, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="donations", help_text="Empty for a donor who isn't an alumnus.")
    donor_name = models.CharField(max_length=200)
    donor_email = models.EmailField(blank=True)
    donor_phone = models.CharField(max_length=32, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    received_on = models.DateField()
    receipt_number = models.CharField(max_length=30)
    reference = models.CharField(max_length=100, blank=True, help_text="Cheque or transfer number.")
    note = models.CharField(max_length=255, blank=True)
    is_anonymous = models.BooleanField(default=False, help_text="Leave the name off public lists.")
    refunded_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    recorded_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        db_table = "alumni_donation"
        ordering = ["-received_on", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "receipt_number"], name="uniq_alumni_receipt"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="alumni_donation_positive"),
        ]

    def __str__(self):
        return self.receipt_number


class DonationRefund(OrganizationOwnedModel):
    donation = models.ForeignKey(Donation, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    refunded_on = models.DateField()
    method = models.CharField(max_length=10, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=255)
    recorded_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")

    class Meta:
        db_table = "alumni_donation_refund"
        ordering = ["-refunded_on", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="alumni_refund_positive"),
        ]
