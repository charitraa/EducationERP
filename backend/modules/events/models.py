"""Events: who's going, who showed up, what they did, and what it earned
them.

An **Event** belongs to one **EventCategory** (sports, cultural, academic
club, …) and optionally one campus (empty: every campus). Registration is
**open** (confirmed straight away), by **approval** (the office decides), or
**none** (no sign-up; attendance is taken for whoever shows up). An optional
capacity closes registration once it's full — counting confirmed
registrations only, never a waitlist.

**EventAttendance** is its own lightweight check-in. This module deliberately
never writes to the attendance module's tables: modules don't modify each
other's data. **EventParticipation** is the richer
record: the role a student played and, for a competition, where they placed.

Both can earn **points**, through a **PointRule** the office configures
(so many points for attending a category, so many for winning it). Every
award of points is one **PointEntry**; **StudentPoints** keeps a running
total in step, the same way ``Invoice.paid_amount`` and
``StaffAttendanceDay`` are kept in step with what feeds them — worked out
once, not recomputed by a background job.

An **Award** (an achievement, a badge, or a title — ``kind`` tells them
apart) is either granted by hand or automatically by an **AwardRule**,
checked the moment the points or counts it depends on change.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.common.models import OrganizationOwnedModel, TimeStampedModel

ALIVE = Q(deleted_at__isnull=True)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
class EventCategory(OrganizationOwnedModel):
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "events_category"
        ordering = ["name", "pk"]
        verbose_name_plural = "event categories"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_event_category_code"),
        ]

    def __str__(self):
        return self.name


class RegistrationMode(models.TextChoices):
    NONE = "none", "No sign-up"
    OPEN = "open", "Open: confirmed straight away"
    APPROVAL = "approval", "Needs approval"


class EventStatus(models.TextChoices):
    DRAFT = "draft", "Being set up"
    PUBLISHED = "published", "Published"
    CANCELLED = "cancelled", "Cancelled"


class Event(OrganizationOwnedModel):
    """One event. ``campus`` empty means every campus."""

    campus = models.ForeignKey("organizations.Campus", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="events")
    category = models.ForeignKey(EventCategory, on_delete=models.PROTECT, related_name="events")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    venue = models.CharField(max_length=200, blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=EventStatus.choices, default=EventStatus.DRAFT, db_index=True)
    registration_mode = models.CharField(max_length=10, choices=RegistrationMode.choices, default=RegistrationMode.OPEN)
    registration_deadline = models.DateTimeField(null=True, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    organized_by = models.ForeignKey("staff.StaffMember", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="organized_events")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "events_event"
        ordering = ["-start_at", "-pk"]
        indexes = [models.Index(fields=["organization", "campus", "start_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(end_at__gte=F("start_at")), name="event_dates_ordered"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_over(self) -> bool:
        from django.utils import timezone

        return self.end_at < timezone.now()

    @property
    def registration_open(self) -> bool:
        from django.utils import timezone

        if self.status != EventStatus.PUBLISHED or self.registration_mode == RegistrationMode.NONE:
            return False
        if self.registration_deadline and timezone.now() > self.registration_deadline:
            return False
        return not self.is_over


class RegistrationStatus(models.TextChoices):
    PENDING = "pending", "Waiting for approval"
    CONFIRMED = "confirmed", "Confirmed"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


class EventRegistration(TimeStampedModel):
    """One student's sign-up for an event."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="registrations")
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="event_registrations")
    status = models.CharField(max_length=10, choices=RegistrationStatus.choices, default=RegistrationStatus.PENDING,
                              db_index=True)
    note = models.CharField(max_length=255, blank=True, help_text="From the student, e.g. why they're applying.")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "events_registration"
        ordering = ["event_id", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["event", "student"], condition=~Q(status="withdrawn"),
                                    name="uniq_live_event_registration"),
        ]

    def __str__(self):
        return f"{self.student} → {self.event}"


# ---------------------------------------------------------------------------
# Attendance and participation
# ---------------------------------------------------------------------------
class AttendanceStatus(models.TextChoices):
    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"


class EventAttendance(TimeStampedModel):
    """A simple check-in: did this student show up? Its own record, never
    the attendance module's ``AttendanceSession``/``AttendanceRecord``."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="attendance")
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="event_attendance")
    status = models.CharField(max_length=10, choices=AttendanceStatus.choices, default=AttendanceStatus.PRESENT)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "events_attendance"
        ordering = ["event_id", "student_id"]
        verbose_name_plural = "event attendance"
        constraints = [models.UniqueConstraint(fields=["event", "student"], name="uniq_event_attendance")]

    def __str__(self):
        return f"{self.student} @ {self.event}: {self.status}"


class ParticipationRole(models.TextChoices):
    PARTICIPANT = "participant", "Participant"
    WINNER = "winner", "Winner"
    RUNNER_UP = "runner_up", "Runner-up"
    ORGANIZER = "organizer", "Organizer"
    VOLUNTEER = "volunteer", "Volunteer"


class EventParticipation(TimeStampedModel):
    """What a student did at an event, beyond just showing up: their role,
    and for a competition, where they placed."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="participation")
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="event_participation")
    role = models.CharField(max_length=12, choices=ParticipationRole.choices, default=ParticipationRole.PARTICIPANT)
    position = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1st, 2nd, 3rd, ... if placed.")
    remark = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "events_participation"
        ordering = ["event_id", "role", "position"]
        constraints = [models.UniqueConstraint(fields=["event", "student", "role"], name="uniq_event_participation")]

    def __str__(self):
        return f"{self.student} — {self.get_role_display()} at {self.event}"


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------
class PointSource(models.TextChoices):
    ATTENDANCE = "attendance", "Attending an event"
    PARTICIPATION = "participation", "A participation role"
    MANUAL = "manual", "Given by hand"


class PointRule(OrganizationOwnedModel):
    """How many points a way of taking part is worth. ``category`` empty:
    any category. ``role`` only matters when ``source`` is participation."""

    name = models.CharField(max_length=200)
    category = models.ForeignKey(EventCategory, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    source = models.CharField(max_length=14, choices=PointSource.choices, default=PointSource.ATTENDANCE)
    role = models.CharField(max_length=12, choices=ParticipationRole.choices, null=True, blank=True)
    points = models.IntegerField()
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "events_point_rule"
        ordering = ["name", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(points__gt=0), name="point_rule_points_positive"),
            models.CheckConstraint(
                condition=(Q(source="participation") | Q(role__isnull=True)),
                name="point_rule_role_needs_participation",
            ),
        ]

    def __str__(self):
        return self.name


class PointEntry(TimeStampedModel):
    """One award of points. Append-only: a mistake is reversed by a new,
    opposite entry, never an edit — the same rule refunds and mark
    corrections already follow."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="point_entries")
    points = models.IntegerField()
    reason = models.CharField(max_length=255)
    rule = models.ForeignKey(PointRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="entries")
    event = models.ForeignKey(Event, null=True, blank=True, on_delete=models.SET_NULL, related_name="point_entries")
    awarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "events_point_entry"
        ordering = ["-created_at", "-pk"]
        constraints = [models.CheckConstraint(condition=~Q(points=0), name="point_entry_not_zero")]

    def __str__(self):
        return f"{self.points:+d} to {self.student} ({self.reason})"


class StudentPoints(models.Model):
    """One row per student: the running total, kept in step with
    ``PointEntry`` inside the same transaction."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    student = models.OneToOneField("students.Student", on_delete=models.CASCADE, related_name="points")
    total = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "events_student_points"
        ordering = ["-total"]

    def __str__(self):
        return f"{self.student}: {self.total}"


# ---------------------------------------------------------------------------
# Awards: achievements, badges, titles
# ---------------------------------------------------------------------------
class AwardKind(models.TextChoices):
    ACHIEVEMENT = "achievement", "Achievement"
    BADGE = "badge", "Badge"
    TITLE = "title", "Title"


class Award(OrganizationOwnedModel):
    """A named recognition a student can hold: an achievement, a badge, or
    a title. Structurally the same; ``kind`` is what tells them apart."""

    kind = models.CharField(max_length=12, choices=AwardKind.choices, default=AwardKind.BADGE)
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text="A short name for the client to pick an icon by.")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "events_award"
        ordering = ["kind", "name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_award_code"),
        ]

    def __str__(self):
        return self.name


class ThresholdKind(models.TextChoices):
    POINTS_TOTAL = "points_total", "Total points"
    EVENTS_ATTENDED = "events_attended", "Events attended"
    EVENTS_WON = "events_won", "Events won"


class AwardRule(OrganizationOwnedModel):
    """When to grant ``award`` automatically. ``category`` narrows an
    events-attended/events-won count to one category; empty counts every
    category."""

    award = models.ForeignKey(Award, on_delete=models.CASCADE, related_name="rules")
    threshold_kind = models.CharField(max_length=16, choices=ThresholdKind.choices)
    threshold_value = models.PositiveIntegerField()
    category = models.ForeignKey(EventCategory, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "events_award_rule"
        ordering = ["award_id", "threshold_value"]
        constraints = [
            models.CheckConstraint(condition=Q(threshold_value__gt=0), name="award_rule_threshold_positive"),
        ]

    def __str__(self):
        return f"{self.award.name}: {self.get_threshold_kind_display()} ≥ {self.threshold_value}"


class StudentAward(TimeStampedModel):
    """One award held by one student. History, like a scholarship grant:
    ``ended_on`` retires it (mainly meaningful for a title) without
    deleting the record."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="awards")
    award = models.ForeignKey(Award, on_delete=models.PROTECT, related_name="holders")
    rule = models.ForeignKey(AwardRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="grants",
                             help_text="Empty: granted by hand.")
    awarded_at = models.DateTimeField()
    ended_on = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "events_student_award"
        ordering = ["-awarded_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["student", "award"], condition=Q(ended_on__isnull=True),
                                    name="uniq_open_student_award"),
        ]

    def __str__(self):
        return f"{self.student} — {self.award.name}"
