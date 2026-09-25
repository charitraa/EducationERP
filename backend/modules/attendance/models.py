"""Attendance: students per roll call, staff per day, and the raw punches
from devices, QR scans and the office that feed them.

Student attendance hangs off a **session**: one roll call of one section on
one date, either the day's (programs in ``daily`` mode) or one lesson's
(``lesson`` mode). Each record points at the student's *enrollment* on that
date, so history stays with the class they were in, whatever happens later.

Staff attendance starts as **punches** (append-only, one per device event),
which are folded into one **staff day** per person and date.

None of these are soft-deleted: attendance is a record of what happened.
Changes after submission are corrections, kept as history.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.common.models import OrganizationOwnedModel, TimeStampedModel

ALIVE = Q(deleted_at__isnull=True)


class AttendanceStatus(models.TextChoices):
    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"
    LATE = "late", "Late"
    EXCUSED = "excused", "Excused"
    LEAVE = "leave", "On leave"
    MEDICAL_LEAVE = "medical_leave", "Medical leave"
    ON_DUTY = "on_duty", "On duty"


# Counted as attended in percentages.
ATTENDED = (AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.ON_DUTY)
# Left out of percentages altogether: neither for nor against the student.
EXCUSED = (AttendanceStatus.EXCUSED, AttendanceStatus.LEAVE, AttendanceStatus.MEDICAL_LEAVE)


class Source(models.TextChoices):
    TEACHER = "teacher", "Teacher"
    MANUAL = "manual", "Office"
    QR = "qr", "QR scan"
    BIOMETRIC = "biometric", "Biometric device"
    API = "api", "API"


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
class AttendanceSession(TimeStampedModel):
    """One roll call: a section's day, or one lesson on one date."""

    class Kind(models.TextChoices):
        DAILY = "daily", "Daily roll call"
        LESSON = "lesson", "Lesson"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        SUBMITTED = "submitted", "Submitted"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="attendance_sessions"
    )
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    section = models.ForeignKey(
        "academics.Section", on_delete=models.PROTECT, related_name="attendance_sessions"
    )
    date = models.DateField()
    kind = models.CharField(max_length=10, choices=Kind.choices)
    timetable_entry = models.ForeignKey(
        "timetable.TimetableEntry", null=True, blank=True, on_delete=models.PROTECT,
        related_name="attendance_sessions", help_text="The lesson, for lesson sessions.",
    )
    teacher = models.ForeignKey(
        "staff.StaffMember", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
        help_text="Who takes it that day: the lesson's teacher after substitutions, or the "
                  "class teacher for a daily roll call.",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "attendance_session"
        ordering = ["-date", "pk"]
        indexes = [
            models.Index(fields=["organization", "date"]),
            models.Index(fields=["section", "date"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["section", "date"], condition=Q(kind="daily"), name="uniq_daily_session"
            ),
            models.UniqueConstraint(
                fields=["timetable_entry", "date"], condition=Q(kind="lesson"),
                name="uniq_lesson_session",
            ),
            models.CheckConstraint(
                condition=(Q(kind="lesson", timetable_entry__isnull=False)
                           | Q(kind="daily", timetable_entry__isnull=True)),
                name="session_entry_matches_kind",
            ),
            models.CheckConstraint(
                condition=(Q(status="submitted", submitted_at__isnull=False)
                           | Q(status="open", submitted_at__isnull=True)),
                name="session_submitted_at_matches_status",
            ),
        ]

    def __str__(self):
        what = "Roll call" if self.kind == self.Kind.DAILY else "Lesson"
        return f"{what} {self.section.display_name} {self.date}"

    @property
    def is_submitted(self) -> bool:
        return self.status == self.Status.SUBMITTED


class AttendanceRecord(TimeStampedModel):
    """One student's attendance in one session."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="attendance_records"
    )
    session = models.ForeignKey(AttendanceSession, on_delete=models.PROTECT, related_name="records")
    enrollment = models.ForeignKey(
        "students.Enrollment", on_delete=models.PROTECT, related_name="attendance_records",
        help_text="The student's stay in the class on that date.",
    )
    status = models.CharField(max_length=20, choices=AttendanceStatus.choices)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.TEACHER)
    note = models.CharField(max_length=255, blank=True)
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Who actually marked it. Empty for a device.",
    )
    recorded_at = models.DateTimeField(
        default=timezone.now, help_text="When it was taken, by the client's clock (offline marking)."
    )
    client_key = models.CharField(
        max_length=64, blank=True,
        help_text="Chosen by the app for each record, so a retried sync never duplicates it.",
    )
    device_id = models.CharField(
        max_length=128, blank=True, help_text="The phone a QR scan came from."
    )

    class Meta:
        db_table = "attendance_record"
        ordering = ["session", "enrollment__student__first_name", "pk"]
        indexes = [models.Index(fields=["organization", "status"])]
        constraints = [
            models.UniqueConstraint(fields=["session", "enrollment"], name="uniq_attendance_record"),
            models.UniqueConstraint(
                fields=["organization", "client_key"], condition=~Q(client_key=""),
                name="uniq_attendance_client_key",
            ),
        ]

    def __str__(self):
        return f"{self.enrollment.student} {self.get_status_display()} — {self.session}"


class AttendanceCorrection(models.Model):
    """A change to a record after its session was submitted. Append-only."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="+"
    )
    record = models.ForeignKey(AttendanceRecord, on_delete=models.CASCADE, related_name="corrections")
    old_status = models.CharField(max_length=20, choices=AttendanceStatus.choices)
    new_status = models.CharField(max_length=20, choices=AttendanceStatus.choices)
    reason = models.CharField(max_length=255)
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    corrected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "attendance_correction"
        ordering = ["corrected_at", "pk"]


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------
class WorkSchedule(OrganizationOwnedModel):
    """Working hours at a campus: when the day starts, when someone is late,
    and which weekdays are working days."""

    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=100)
    start_time = models.TimeField()
    end_time = models.TimeField()
    grace_minutes = models.PositiveSmallIntegerField(
        default=10, help_text="Arriving later than this after the start is late."
    )
    half_day_minutes = models.PositiveSmallIntegerField(
        default=240, help_text="Working less than this, with a check-out, is a half day."
    )
    weekdays = models.JSONField(
        default=list, help_text="Working days, ISO numbers: 1 = Monday … 7 = Sunday."
    )
    is_default = models.BooleanField(
        default=False, help_text="Used for the campus's staff who have no schedule of their own."
    )

    class Meta:
        db_table = "attendance_work_schedule"
        ordering = ["campus", "name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["campus"], condition=ALIVE & Q(is_default=True),
                name="uniq_default_work_schedule",
            ),
            models.CheckConstraint(
                condition=Q(end_time__gt=models.F("start_time")), name="work_schedule_times_ordered"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.start_time:%H:%M}–{self.end_time:%H:%M})"


class StaffWorkSchedule(TimeStampedModel):
    """A staff member's own schedule, instead of their campus's default."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="+"
    )
    staff = models.OneToOneField(
        "staff.StaffMember", on_delete=models.CASCADE, related_name="work_schedule"
    )
    schedule = models.ForeignKey(WorkSchedule, on_delete=models.PROTECT, related_name="staff")

    class Meta:
        db_table = "attendance_staff_work_schedule"
        ordering = ["pk"]


class AttendanceDevice(OrganizationOwnedModel):
    """A biometric reader or other machine that sends punches."""

    class Kind(models.TextChoices):
        ZKTECO = "zkteco", "ZKTeco (push protocol)"
        GENERIC = "generic", "Generic (device API)"

    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    serial_number = models.CharField(max_length=64, help_text="As printed on the device.")
    key_hash = models.CharField(max_length=128, blank=True, help_text="Generic devices' API key.")
    key_prefix = models.CharField(max_length=12, blank=True)
    allowed_ips = models.JSONField(
        default=list, blank=True,
        help_text="If set, only these addresses may send as this device. Recommended for "
                  "ZKTeco, whose protocol identifies a device by its serial number alone.",
    )
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "attendance_device"
        ordering = ["campus", "name", "pk"]
        constraints = [
            # A ZKTeco device names itself only by serial number, so it must
            # identify one device across every organization.
            models.UniqueConstraint(
                fields=["serial_number"], condition=ALIVE, name="uniq_device_serial"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.serial_number})"


class BiometricIdentity(TimeStampedModel):
    """Who a user number (PIN) enrolled on the devices is."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="+"
    )
    pin = models.CharField(max_length=32)
    staff = models.OneToOneField(
        "staff.StaffMember", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    student = models.OneToOneField(
        "students.Student", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )

    class Meta:
        db_table = "attendance_biometric_identity"
        ordering = ["pin"]
        verbose_name_plural = "biometric identities"
        constraints = [
            models.UniqueConstraint(fields=["organization", "pin"], name="uniq_biometric_pin"),
            models.CheckConstraint(
                condition=(Q(staff__isnull=False, student__isnull=True)
                           | Q(staff__isnull=True, student__isnull=False)),
                name="biometric_identity_is_one_person",
            ),
        ]

    def __str__(self):
        return f"PIN {self.pin}"


class Punch(models.Model):
    """One raw event: a finger on a reader, a QR scan at the gate, or an
    entry by the office. Append-only; staff days are worked out from these."""

    class Direction(models.TextChoices):
        IN = "in", "In"
        OUT = "out", "Out"
        UNKNOWN = "unknown", "Unknown"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="+"
    )
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="+")
    device = models.ForeignKey(
        AttendanceDevice, null=True, blank=True, on_delete=models.PROTECT, related_name="punches"
    )
    pin = models.CharField(max_length=32, blank=True)
    staff = models.ForeignKey(
        "staff.StaffMember", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    student = models.ForeignKey(
        "students.Student", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    punched_at = models.DateTimeField()
    direction = models.CharField(max_length=10, choices=Direction.choices, default=Direction.UNKNOWN)
    source = models.CharField(max_length=20, choices=Source.choices)
    verify = models.CharField(max_length=20, blank=True, help_text="fingerprint, face, card …")
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.CharField(max_length=255, blank=True)
    dedupe_key = models.CharField(
        max_length=200, help_text="The same event sent twice has the same key."
    )
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "attendance_punch"
        ordering = ["-punched_at", "-pk"]
        verbose_name_plural = "punches"
        indexes = [
            models.Index(fields=["staff", "punched_at"]),
            models.Index(fields=["organization", "pin"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["organization", "dedupe_key"], name="uniq_punch"),
        ]

    def __str__(self):
        return f"{self.staff or self.student or 'PIN ' + self.pin} at {self.punched_at}"


class StaffAttendanceDay(TimeStampedModel):
    """A staff member's day, worked out from their punches, unless the
    office has set it by hand (``is_override``)."""

    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        LATE = "late", "Late"
        HALF_DAY = "half_day", "Half day"
        ABSENT = "absent", "Absent"
        LEAVE = "leave", "On leave"
        ON_DUTY = "on_duty", "On duty"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="+"
    )
    staff = models.ForeignKey("staff.StaffMember", on_delete=models.PROTECT, related_name="+")
    date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices)
    first_in = models.DateTimeField(null=True, blank=True)
    last_out = models.DateTimeField(null=True, blank=True)
    worked_minutes = models.PositiveIntegerField(null=True, blank=True)
    is_override = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)
    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "attendance_staff_day"
        ordering = ["-date", "staff__first_name", "pk"]
        indexes = [models.Index(fields=["organization", "date"])]
        constraints = [
            models.UniqueConstraint(fields=["staff", "date"], name="uniq_staff_day"),
        ]

    def __str__(self):
        return f"{self.staff} {self.date} {self.get_status_display()}"
