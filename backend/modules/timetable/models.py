"""The weekly timetable: when each teaching assignment meets, and where.

A campus defines one or more **bell schedules** ("Day shift", "Morning
shift"), each a list of **periods** with clock times. A **timetable entry**
puts a teaching assignment (section + subject + teacher) into one period on
one weekday, optionally in a room and optionally for one term only.

Clashes are found by clock time, not by period, so two shifts with different
bells at the same campus are still checked against each other.

History stays true: a lesson or period that changes after it has run is
not edited in place. The old row ends (``valid_until``) and a new one starts
(``valid_from``), so what happened on any past date can still be read.

Entries sharing a ``combined_group`` are one **combined class**: a teacher
teaching several sections together, in one room, at one time. A
**lesson change** alters one entry on one date: a substitute teacher, another
room, or a cancellation.
"""
from django.db import models
from django.db.models import F, Q

from core.common.models import OrganizationOwnedModel

ALIVE = Q(deleted_at__isnull=True)


class Weekday(models.IntegerChoices):
    """ISO numbering, as ``date.isoweekday()`` returns it."""

    MONDAY = 1, "Monday"
    TUESDAY = 2, "Tuesday"
    WEDNESDAY = 3, "Wednesday"
    THURSDAY = 4, "Thursday"
    FRIDAY = 5, "Friday"
    SATURDAY = 6, "Saturday"
    SUNDAY = 7, "Sunday"


class BellSchedule(OrganizationOwnedModel):
    """A campus's set of periods, e.g. "Day shift" or "Morning (+2)"."""

    campus = models.ForeignKey(
        "organizations.Campus", on_delete=models.PROTECT, related_name="bell_schedules"
    )
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "timetable_bell_schedule"
        ordering = ["campus", "name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["campus", "name"], condition=ALIVE, name="uniq_bell_schedule_name"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.campus.name})"


class Period(OrganizationOwnedModel):
    """One slot of a bell schedule: "Period 1" 10:00–10:45, or a break."""

    schedule = models.ForeignKey(BellSchedule, on_delete=models.CASCADE, related_name="periods")
    name = models.CharField(max_length=50)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_break = models.BooleanField(default=False, help_text="Breaks can't have lessons.")
    valid_from = models.DateField(null=True, blank=True, help_text="Empty: always.")
    valid_until = models.DateField(
        null=True, blank=True, help_text="Set when the bell times change, e.g. winter timings."
    )

    class Meta:
        db_table = "timetable_period"
        ordering = ["schedule", "start_time", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")), name="period_times_ordered"
            ),
            models.CheckConstraint(
                condition=Q(valid_from__isnull=True) | Q(valid_until__isnull=True)
                | Q(valid_until__gte=F("valid_from")),
                name="period_validity_ordered",
            ),
        ]

    def __str__(self):
        return f"{self.name} {self.start_time:%H:%M}–{self.end_time:%H:%M}"


class TimetableEntry(OrganizationOwnedModel):
    """A weekly lesson: this teaching assignment meets in this period on
    this weekday. With a ``term`` it only runs that term; without, all year.

    Attendance (Phase 4) is taken against these.
    """

    teaching_assignment = models.ForeignKey(
        "academics.TeachingAssignment", on_delete=models.CASCADE, related_name="timetable_entries"
    )
    day_of_week = models.PositiveSmallIntegerField(choices=Weekday.choices)
    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name="timetable_entries")
    room = models.ForeignKey(
        "academics.Room", null=True, blank=True, on_delete=models.PROTECT,
        related_name="timetable_entries",
    )
    term = models.ForeignKey(
        "academics.Term", null=True, blank=True, on_delete=models.PROTECT,
        related_name="timetable_entries", help_text="Empty: runs the whole academic year.",
    )
    combined_group = models.UUIDField(
        null=True, blank=True, db_index=True,
        help_text="Entries with the same value are one combined class of several sections.",
    )
    valid_from = models.DateField(
        null=True, blank=True, help_text="First day the lesson runs. Empty: from the start of the year/term."
    )
    valid_until = models.DateField(
        null=True, blank=True, help_text="Last day the lesson runs. Empty: to the end of the year/term."
    )

    class Meta:
        db_table = "timetable_entry"
        ordering = ["day_of_week", "period__start_time", "pk"]
        verbose_name_plural = "timetable entries"
        indexes = [models.Index(fields=["organization", "day_of_week"])]
        # Overlaps (the same teacher, room or section twice at once, and so
        # an exact duplicate too) are checked in the service under row locks:
        # "overlapping dates and times" can't be a portable constraint.
        constraints = [
            models.CheckConstraint(
                condition=Q(day_of_week__gte=1, day_of_week__lte=7), name="timetable_entry_weekday"
            ),
            models.CheckConstraint(
                condition=Q(valid_from__isnull=True) | Q(valid_until__isnull=True)
                | Q(valid_until__gte=F("valid_from")),
                name="timetable_entry_validity_ordered",
            ),
        ]

    def __str__(self):
        return f"{self.get_day_of_week_display()} {self.period}: {self.teaching_assignment}"


class LessonChange(OrganizationOwnedModel):
    """A change to one lesson on one date: a substitute teacher, another
    room, or the lesson is cancelled. The weekly entry itself stays as is."""

    entry = models.ForeignKey(TimetableEntry, on_delete=models.CASCADE, related_name="changes")
    date = models.DateField(db_index=True)
    is_cancelled = models.BooleanField(default=False)
    substitute_teacher = models.ForeignKey(
        "staff.StaffMember", null=True, blank=True, on_delete=models.PROTECT,
        related_name="substitutions",
    )
    room = models.ForeignKey(
        "academics.Room", null=True, blank=True, on_delete=models.PROTECT,
        related_name="lesson_changes", help_text="Where the lesson moves to that day.",
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "timetable_lesson_change"
        ordering = ["date", "entry__period__start_time", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["entry", "date"], condition=ALIVE, name="uniq_lesson_change"),
            models.CheckConstraint(
                condition=(
                    Q(is_cancelled=True, substitute_teacher__isnull=True, room__isnull=True)
                    | (Q(is_cancelled=False)
                       & (Q(substitute_teacher__isnull=False) | Q(room__isnull=False)))
                ),
                name="lesson_change_is_meaningful",
            ),
        ]

    def __str__(self):
        return f"{self.date}: {self.entry}"
