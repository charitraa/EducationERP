"""Examinations: how marks become grades, results and transcripts.

The chain is  **Exam → ExamSubject (+ components) → MarkSheet → Mark → Result**.

* A **GradeScale** (per program, or the organization's default) turns a
  percentage into a letter, a grade point and pass/fail.
* An **Exam** is one sitting for a program: its papers (ExamSubject, one per
  subject per level, each with components such as theory and practical that
  have their own full and pass marks), rooms, seats and admit cards.
* Teachers enter **Marks** on a **MarkSheet** (one per paper per section); the
  office verifies each sheet, then publishes the exam.
* A **Result** is computed and stored per student, so a published result
  doesn't move when a grade scale is edited later. A **ResultPlan** combines
  several exams by weight (unit tests 20% + terminal 80%) into a term result.

Configuration is soft-deleted like the rest of the system. Marks, sheets and
results are records of what happened and are never deleted: a change after
verification is a correction, kept as history.
"""
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.common.models import OrganizationOwnedModel, TimeStampedModel

ALIVE = Q(deleted_at__isnull=True)


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------
class GradeScale(OrganizationOwnedModel):
    """Bands that turn a percentage into a grade.

    ``program`` empty makes it the organization's default, used by every
    program without a scale of its own.
    """

    program = models.ForeignKey(
        "academics.Program", null=True, blank=True, on_delete=models.CASCADE, related_name="grade_scales",
        help_text="Empty: the organization's default scale.",
    )
    name = models.CharField(max_length=100)
    max_grade_point = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("4.00"))
    require_all_subjects_pass = models.BooleanField(
        default=True, help_text="One failed subject fails the whole result.",
    )
    overall_pass_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="If set, an overall percentage below this fails the result too.",
    )

    class Meta:
        db_table = "examinations_grade_scale"
        ordering = ["program__name", "name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "program"], condition=ALIVE & Q(program__isnull=False),
                name="uniq_grade_scale_per_program",
            ),
            models.UniqueConstraint(
                fields=["organization"], condition=ALIVE & Q(program__isnull=True),
                name="uniq_default_grade_scale",
            ),
        ]

    def __str__(self):
        return self.name


class GradeBand(models.Model):
    """From ``min_percentage`` upwards, up to the next band."""

    scale = models.ForeignKey(GradeScale, on_delete=models.CASCADE, related_name="bands")
    min_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    letter = models.CharField(max_length=10)
    grade_point = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0"))
    remark = models.CharField(max_length=100, blank=True)
    is_pass = models.BooleanField(default=True)

    class Meta:
        db_table = "examinations_grade_band"
        ordering = ["scale_id", "-min_percentage"]
        constraints = [
            models.UniqueConstraint(fields=["scale", "min_percentage"], name="uniq_grade_band_min"),
            models.CheckConstraint(
                condition=Q(min_percentage__gte=0, min_percentage__lte=100), name="grade_band_percentage_range"
            ),
        ]

    def __str__(self):
        return f"{self.letter} (from {self.min_percentage}%)"


class DivisionBand(models.Model):
    """Overall classes such as Distinction or First Division."""

    scale = models.ForeignKey(GradeScale, on_delete=models.CASCADE, related_name="divisions")
    min_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    name = models.CharField(max_length=50)

    class Meta:
        db_table = "examinations_division_band"
        ordering = ["scale_id", "-min_percentage"]
        constraints = [
            models.UniqueConstraint(fields=["scale", "min_percentage"], name="uniq_division_band_min"),
        ]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Exams
# ---------------------------------------------------------------------------
class ExamType(OrganizationOwnedModel):
    """A kind of exam: unit test, first terminal, final, pre-board."""

    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "examinations_exam_type"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], condition=ALIVE, name="uniq_exam_type_code"),
        ]

    def __str__(self):
        return self.name


class Exam(OrganizationOwnedModel):
    """One exam for one program at one campus and academic year."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Being set up"
        SCHEDULED = "scheduled", "Scheduled"
        PUBLISHED = "published", "Results published"

    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="exams")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="exams")
    term = models.ForeignKey("academics.Term", null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="exams")
    exam_type = models.ForeignKey(ExamType, on_delete=models.PROTECT, related_name="exams")
    program = models.ForeignKey("academics.Program", on_delete=models.PROTECT, related_name="exams")
    name = models.CharField(max_length=200)
    grade_scale = models.ForeignKey(GradeScale, on_delete=models.PROTECT, related_name="exams")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    min_attendance_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Students below this attendance get their admit card withheld.",
    )
    instructions = models.TextField(blank=True, help_text="Printed on every admit card.")
    on_transcript = models.BooleanField(
        default=False, help_text="Include this exam's own result on transcripts.",
    )
    calendar_event = models.ForeignKey(
        "academics.CalendarEvent", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "examinations_exam"
        ordering = ["-start_date", "name", "pk"]
        indexes = [models.Index(fields=["organization", "academic_year", "campus"])]
        constraints = [
            models.UniqueConstraint(
                fields=["campus", "academic_year", "name"], condition=ALIVE, name="uniq_exam_name",
            ),
            models.CheckConstraint(
                condition=Q(start_date__isnull=True) | Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="exam_dates_ordered",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_published(self) -> bool:
        return self.status == self.Status.PUBLISHED


class ExamSubject(TimeStampedModel):
    """One paper: a subject at one level in an exam, with its date and time."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="exam_subjects"
    )
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="subjects")
    subject = models.ForeignKey("academics.Subject", on_delete=models.PROTECT, related_name="exam_subjects")
    level = models.PositiveSmallIntegerField()
    date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    credit_hours = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text="Weight in the GPA. Empty: the subject's credit hours, or 1.",
    )

    class Meta:
        db_table = "examinations_exam_subject"
        ordering = ["exam_id", "level", "date", "start_time", "subject__name"]
        constraints = [
            models.UniqueConstraint(fields=["exam", "level", "subject"], name="uniq_exam_subject"),
            models.CheckConstraint(
                condition=Q(start_time__isnull=True) | Q(end_time__isnull=True) | Q(end_time__gt=F("start_time")),
                name="exam_subject_times_ordered",
            ),
        ]

    def __str__(self):
        return f"{self.exam.name}: {self.subject.name} (level {self.level})"

    @property
    def weight(self) -> Decimal:
        return self.credit_hours or self.subject.credit_hours or Decimal("1")


class ExamComponent(TimeStampedModel):
    """A part of a paper marked separately: theory, practical, internal."""

    class Kind(models.TextChoices):
        THEORY = "theory", "Theory"
        PRACTICAL = "practical", "Practical"
        INTERNAL = "internal", "Internal assessment"
        VIVA = "viva", "Viva"
        PROJECT = "project", "Project"
        OTHER = "other", "Other"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="exam_components"
    )
    exam_subject = models.ForeignKey(ExamSubject, on_delete=models.CASCADE, related_name="components")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.THEORY)
    name = models.CharField(max_length=50)
    full_marks = models.DecimalField(max_digits=6, decimal_places=2)
    pass_marks = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "examinations_exam_component"
        ordering = ["exam_subject_id", "order", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["exam_subject", "name"], name="uniq_exam_component_name"),
            models.CheckConstraint(condition=Q(full_marks__gt=0), name="exam_component_full_positive"),
            models.CheckConstraint(
                condition=Q(pass_marks__gte=0, pass_marks__lte=F("full_marks")), name="exam_component_pass_in_range"
            ),
        ]

    def __str__(self):
        return f"{self.exam_subject}: {self.name}"


# ---------------------------------------------------------------------------
# Rooms, seats, invigilators, admit cards
# ---------------------------------------------------------------------------
class ExamRoom(TimeStampedModel):
    """A room used for an exam, with how many candidates it takes."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="exam_rooms"
    )
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="rooms")
    room = models.ForeignKey("academics.Room", on_delete=models.PROTECT, related_name="exam_uses")
    capacity = models.PositiveIntegerField(
        null=True, blank=True, help_text="Seats for this exam. Empty: the room's capacity."
    )

    class Meta:
        db_table = "examinations_exam_room"
        ordering = ["exam_id", "room__code"]
        constraints = [models.UniqueConstraint(fields=["exam", "room"], name="uniq_exam_room")]

    def __str__(self):
        return f"{self.exam.name} in {self.room.name}"

    @property
    def seat_count(self) -> int | None:
        return self.capacity if self.capacity is not None else self.room.capacity


class SeatAllocation(TimeStampedModel):
    """Where a student sits for the whole exam."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="seat_allocations"
    )
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="seats")
    enrollment = models.ForeignKey("students.Enrollment", on_delete=models.PROTECT, related_name="exam_seats")
    exam_room = models.ForeignKey(ExamRoom, on_delete=models.CASCADE, related_name="seats")
    seat_number = models.PositiveIntegerField()

    class Meta:
        db_table = "examinations_seat_allocation"
        ordering = ["exam_room_id", "seat_number"]
        constraints = [
            models.UniqueConstraint(fields=["exam", "enrollment"], name="uniq_seat_per_student"),
            models.UniqueConstraint(fields=["exam_room", "seat_number"], name="uniq_seat_number"),
        ]


class Invigilation(TimeStampedModel):
    """A staff member watching one room during one paper."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="invigilations"
    )
    exam_subject = models.ForeignKey(ExamSubject, on_delete=models.CASCADE, related_name="invigilations")
    exam_room = models.ForeignKey(ExamRoom, on_delete=models.CASCADE, related_name="invigilations")
    staff = models.ForeignKey("staff.StaffMember", on_delete=models.PROTECT, related_name="invigilations")
    is_chief = models.BooleanField(default=False)

    class Meta:
        db_table = "examinations_invigilation"
        ordering = ["exam_subject_id", "exam_room_id", "-is_chief", "staff_id"]
        constraints = [
            models.UniqueConstraint(fields=["exam_subject", "exam_room", "staff"], name="uniq_invigilation"),
        ]


class AdmitCard(TimeStampedModel):
    class Status(models.TextChoices):
        ISSUED = "issued", "Issued"
        WITHHELD = "withheld", "Withheld"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="admit_cards"
    )
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="admit_cards")
    enrollment = models.ForeignKey("students.Enrollment", on_delete=models.PROTECT, related_name="admit_cards")
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="admit_cards")
    card_number = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ISSUED)
    withheld_reason = models.CharField(max_length=255, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "examinations_admit_card"
        ordering = ["exam_id", "card_number"]
        constraints = [
            models.UniqueConstraint(fields=["exam", "enrollment"], name="uniq_admit_card_per_student"),
            models.UniqueConstraint(fields=["exam", "card_number"], name="uniq_admit_card_number"),
        ]

    def __str__(self):
        return self.card_number


# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------
class MarkSheet(TimeStampedModel):
    """The marks of one paper for one section: what a teacher enters, submits
    and the office verifies."""

    class Status(models.TextChoices):
        OPEN = "open", "Being entered"
        SUBMITTED = "submitted", "Submitted"
        VERIFIED = "verified", "Verified"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="mark_sheets"
    )
    exam_subject = models.ForeignKey(ExamSubject, on_delete=models.CASCADE, related_name="sheets")
    section = models.ForeignKey("academics.Section", on_delete=models.PROTECT, related_name="mark_sheets")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    review_note = models.CharField(max_length=255, blank=True, help_text="Why the office sent it back.")

    class Meta:
        db_table = "examinations_mark_sheet"
        ordering = ["exam_subject_id", "section_id"]
        constraints = [
            models.UniqueConstraint(fields=["exam_subject", "section"], name="uniq_mark_sheet"),
        ]

    def __str__(self):
        return f"{self.exam_subject} — {self.section.display_name}"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN


class MarkStatus(models.TextChoices):
    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"
    EXEMPT = "exempt", "Exempt"
    WITHHELD = "withheld", "Withheld"


class Mark(TimeStampedModel):
    """One student's mark in one component."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="marks")
    sheet = models.ForeignKey(MarkSheet, on_delete=models.CASCADE, related_name="marks")
    component = models.ForeignKey(ExamComponent, on_delete=models.PROTECT, related_name="marks")
    enrollment = models.ForeignKey("students.Enrollment", on_delete=models.PROTECT, related_name="marks")
    status = models.CharField(max_length=10, choices=MarkStatus.choices, default=MarkStatus.PRESENT)
    marks = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "examinations_mark"
        ordering = ["sheet_id", "enrollment_id", "component_id"]
        constraints = [
            models.UniqueConstraint(fields=["component", "enrollment"], name="uniq_mark"),
            # Only a present student has marks.
            models.CheckConstraint(
                condition=(Q(status="present", marks__isnull=False, marks__gte=0)
                           | (~Q(status="present") & Q(marks__isnull=True))),
                name="mark_value_matches_status",
            ),
        ]


class MarkCorrection(TimeStampedModel):
    """A change to a mark after its sheet left the teacher's hands."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="mark_corrections"
    )
    mark = models.ForeignKey(Mark, on_delete=models.CASCADE, related_name="corrections")
    old_status = models.CharField(max_length=10, choices=MarkStatus.choices)
    old_marks = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    new_status = models.CharField(max_length=10, choices=MarkStatus.choices)
    new_marks = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    reason = models.CharField(max_length=255)
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "examinations_mark_correction"
        ordering = ["mark_id", "created_at"]

    @property
    def corrected_at(self) -> datetime:
        return self.created_at


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
class ResultPlan(OrganizationOwnedModel):
    """A term result made from several exams, each counting for a share."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="result_plans")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT,
                                      related_name="result_plans")
    term = models.ForeignKey("academics.Term", null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="result_plans")
    program = models.ForeignKey("academics.Program", on_delete=models.PROTECT, related_name="result_plans")
    name = models.CharField(max_length=200)
    grade_scale = models.ForeignKey(GradeScale, on_delete=models.PROTECT, related_name="result_plans")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    on_transcript = models.BooleanField(default=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "examinations_result_plan"
        ordering = ["-academic_year__start_date", "name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["campus", "academic_year", "name"], condition=ALIVE, name="uniq_result_plan_name",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_published(self) -> bool:
        return self.status == self.Status.PUBLISHED


class ResultPlanExam(models.Model):
    plan = models.ForeignKey(ResultPlan, on_delete=models.CASCADE, related_name="items")
    exam = models.ForeignKey(Exam, on_delete=models.PROTECT, related_name="plan_items")
    weight = models.DecimalField(max_digits=5, decimal_places=2, help_text="Percent of the term result.")

    class Meta:
        db_table = "examinations_result_plan_exam"
        ordering = ["plan_id", "exam__start_date", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "exam"], name="uniq_plan_exam"),
            models.CheckConstraint(condition=Q(weight__gt=0, weight__lte=100), name="plan_exam_weight_range"),
        ]


class ResultStatus(models.TextChoices):
    PASS = "pass", "Pass"
    FAIL = "fail", "Fail"
    WITHHELD = "withheld", "Withheld"
    INCOMPLETE = "incomplete", "Marks missing"
    EXEMPT = "exempt", "Exempt"


class Result(TimeStampedModel):
    """A student's result in one exam, or in one plan. Stored, so publishing
    freezes it; corrections recompute it."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="results")
    exam = models.ForeignKey(Exam, null=True, blank=True, on_delete=models.CASCADE, related_name="results")
    plan = models.ForeignKey(ResultPlan, null=True, blank=True, on_delete=models.CASCADE, related_name="results")
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="exam_results")
    enrollment = models.ForeignKey("students.Enrollment", on_delete=models.PROTECT, related_name="exam_results")
    section = models.ForeignKey("academics.Section", on_delete=models.PROTECT, related_name="exam_results")
    total_obtained = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0"))
    total_full = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0"))
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    grade_point = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0"))
    letter = models.CharField(max_length=10, blank=True)
    division = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=12, choices=ResultStatus.choices, default=ResultStatus.INCOMPLETE)
    rank_in_section = models.PositiveIntegerField(null=True, blank=True)
    rank_in_level = models.PositiveIntegerField(null=True, blank=True)
    remark = models.CharField(max_length=500, blank=True)
    remark_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "examinations_result"
        ordering = ["section_id", "-percentage", "student__first_name", "pk"]
        indexes = [models.Index(fields=["student", "exam"]), models.Index(fields=["student", "plan"])]
        constraints = [
            models.CheckConstraint(
                condition=(Q(exam__isnull=False, plan__isnull=True) | Q(exam__isnull=True, plan__isnull=False)),
                name="result_exam_xor_plan",
            ),
            models.UniqueConstraint(fields=["exam", "enrollment"], condition=Q(exam__isnull=False),
                                    name="uniq_exam_result"),
            models.UniqueConstraint(fields=["plan", "enrollment"], condition=Q(plan__isnull=False),
                                    name="uniq_plan_result"),
        ]

    @property
    def source(self):
        return self.exam or self.plan

    @property
    def is_published(self) -> bool:
        return self.source.is_published


class SubjectResult(models.Model):
    """One subject's line on a result, with the components that made it."""

    result = models.ForeignKey(Result, on_delete=models.CASCADE, related_name="subjects")
    subject = models.ForeignKey("academics.Subject", on_delete=models.PROTECT, related_name="+")
    exam_subject = models.ForeignKey(ExamSubject, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    credit_hours = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal("1"))
    obtained = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0"))
    full = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0"))
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    letter = models.CharField(max_length=10, blank=True)
    grade_point = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0"))
    remark = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=12, choices=ResultStatus.choices, default=ResultStatus.PASS)
    absent = models.BooleanField(default=False)
    detail = models.JSONField(default=list, blank=True,
                              help_text="Components (or, for a plan, the exams) that made this line.")

    class Meta:
        db_table = "examinations_subject_result"
        ordering = ["result_id", "subject__name"]
        constraints = [models.UniqueConstraint(fields=["result", "subject"], name="uniq_subject_result")]
