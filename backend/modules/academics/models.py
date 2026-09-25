"""The academic structure: what is taught, when, where and to which groups.

One model covers schools (Grade 1–10, sections A/B), +2 colleges (Grade 11–12
per stream) and bachelor/master programs (Semester 1–8): a Program has a
numbered range of *levels*, and a Section is one teaching group at one level
of one program, for one academic year, at one campus.

Dates are stored in AD; names are free text, so Bikram Sambat labels such as
"2082/83" work as they are.
"""
from django.db import models
from django.db.models import F, Q

from core.common.models import OrganizationOwnedModel, TimeStampedModel

ALIVE = Q(deleted_at__isnull=True)


class Department(OrganizationOwnedModel):
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    head = models.ForeignKey(
        "staff.StaffMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="headed_departments",
    )

    class Meta:
        db_table = "academics_department"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], condition=ALIVE, name="uniq_department_code"
            ),
        ]

    def __str__(self):
        return self.name


class Program(OrganizationOwnedModel):
    """A course of study with numbered levels.

    Examples: "Secondary School" Grade 1–10; "+2 Science" Grade 11–12;
    "BSc CSIT" Semester 1–8; "Montessori" Grade 0–0.
    """

    class LevelType(models.TextChoices):
        GRADE = "grade", "Grade"
        SEMESTER = "semester", "Semester"
        YEAR = "year", "Year"
        TRIMESTER = "trimester", "Trimester"

    class AttendanceMode(models.TextChoices):
        DAILY = "daily", "Once a day, by the class teacher"
        LESSON = "lesson", "In every lesson, by its teacher"

    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=200)
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="programs"
    )
    level_type = models.CharField(max_length=20, choices=LevelType.choices, default=LevelType.GRADE)
    first_level = models.PositiveSmallIntegerField(default=1)
    last_level = models.PositiveSmallIntegerField(default=1)
    description = models.TextField(blank=True)
    attendance_mode = models.CharField(
        max_length=10, choices=AttendanceMode.choices, default=AttendanceMode.DAILY,
        help_text="Schools usually take one roll call a day; colleges take it every lesson.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "academics_program"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], condition=ALIVE, name="uniq_program_code"
            ),
            models.CheckConstraint(
                condition=Q(last_level__gte=F("first_level")), name="program_levels_ordered"
            ),
        ]

    def __str__(self):
        return self.name

    def has_level(self, level: int) -> bool:
        return self.first_level <= level <= self.last_level

    def level_label(self, level: int) -> str:
        return f"{self.get_level_type_display()} {level}"


class Subject(OrganizationOwnedModel):
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=200)
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="subjects"
    )
    credit_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "academics_subject"
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], condition=ALIVE, name="uniq_subject_code"
            ),
            models.CheckConstraint(
                condition=Q(credit_hours__isnull=True) | Q(credit_hours__gt=0),
                name="subject_credit_hours_positive",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


class CurriculumSubject(TimeStampedModel):
    """A subject taught at one level of a program — the program's syllabus plan."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="curriculum_subjects"
    )
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="curriculum")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="curriculum_entries")
    level = models.PositiveSmallIntegerField()
    is_elective = models.BooleanField(default=False)

    class Meta:
        db_table = "academics_curriculum_subject"
        ordering = ["program", "level", "subject__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "level", "subject"], name="uniq_curriculum_subject"
            ),
        ]

    def __str__(self):
        return f"{self.program} — {self.program.level_label(self.level)}: {self.subject.name}"


class AcademicYear(OrganizationOwnedModel):
    name = models.CharField(max_length=50, help_text='Free text, e.g. "2082/83" or "2026-27".')
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)

    class Meta:
        db_table = "academics_academic_year"
        ordering = ["-start_date", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"], condition=ALIVE, name="uniq_academic_year_name"
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=Q(is_current=True, deleted_at__isnull=True),
                name="uniq_current_academic_year",
            ),
            models.CheckConstraint(
                condition=Q(end_date__gt=F("start_date")), name="academic_year_dates_ordered"
            ),
        ]

    def __str__(self):
        return self.name


class Term(OrganizationOwnedModel):
    """A part of an academic year: a semester, trimester or exam term."""

    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="terms")
    name = models.CharField(max_length=100)
    sequence = models.PositiveSmallIntegerField(help_text="1 for the first term of the year.")
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        db_table = "academics_term"
        ordering = ["academic_year", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "sequence"], condition=ALIVE, name="uniq_term_sequence"
            ),
            models.CheckConstraint(
                condition=Q(end_date__gt=F("start_date")), name="term_dates_ordered"
            ),
        ]

    def __str__(self):
        return f"{self.academic_year} — {self.name}"


class Batch(OrganizationOwnedModel):
    """An intake cohort that moves through a program together, e.g.
    "BSc CSIT 2082". Mostly used by colleges; schools can ignore it."""

    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=200)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="batches")
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="batches")
    start_year = models.ForeignKey(
        AcademicYear, on_delete=models.PROTECT, related_name="intake_batches",
        help_text="The academic year this cohort started.",
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "academics_batch"
        ordering = ["-start_year__start_date", "name", "pk"]
        verbose_name_plural = "batches"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], condition=ALIVE, name="uniq_batch_code"
            ),
        ]

    def __str__(self):
        return self.name


class Room(OrganizationOwnedModel):
    class RoomType(models.TextChoices):
        CLASSROOM = "classroom", "Classroom"
        LAB = "lab", "Laboratory"
        HALL = "hall", "Hall"
        LIBRARY = "library", "Library"
        OFFICE = "office", "Office"
        OTHER = "other", "Other"

    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="rooms")
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    building = models.CharField(max_length=100, blank=True)
    floor = models.CharField(max_length=20, blank=True)
    room_type = models.CharField(max_length=20, choices=RoomType.choices, default=RoomType.CLASSROOM)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "academics_room"
        ordering = ["campus", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["campus", "code"], condition=ALIVE, name="uniq_room_code_per_campus"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.campus.name})"


class Section(OrganizationOwnedModel):
    """One teaching group: e.g. "Grade 10 A" at Baneshwor for 2082/83, or
    "BSc CSIT Semester 3 A" for the 2082 batch."""

    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="sections")
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="sections")
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="sections")
    level = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=50, help_text='Usually a letter: "A", "B".')
    batch = models.ForeignKey(
        Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="sections"
    )
    class_teacher = models.ForeignKey(
        "staff.StaffMember", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="class_teacher_of",
    )
    home_room = models.ForeignKey(
        Room, null=True, blank=True, on_delete=models.SET_NULL, related_name="home_sections"
    )
    capacity = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "academics_section"
        ordering = ["program__name", "level", "name", "pk"]
        indexes = [models.Index(fields=["organization", "academic_year", "campus"])]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "campus", "program", "level", "name"],
                condition=ALIVE,
                name="uniq_section",
            ),
        ]

    def __str__(self):
        return f"{self.program.level_label(self.level)} {self.name} — {self.program.name} ({self.academic_year})"

    @property
    def display_name(self) -> str:
        return f"{self.program.level_label(self.level)} {self.name}"


class TeachingAssignment(TimeStampedModel):
    """Who teaches which subject to which section. The timetable (Phase 3b)
    schedules these; attendance and marks hang off them later.

    One subject may have several teachers: theory by one, practicals by
    another, or two teaching together. ``role`` tells them apart.
    """

    class Role(models.TextChoices):
        LECTURE = "lecture", "Lecture / theory"
        PRACTICAL = "practical", "Practical / lab"
        TUTORIAL = "tutorial", "Tutorial"
        CO_TEACHING = "co_teaching", "Co-teaching"

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="teaching_assignments"
    )
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="teaching_assignments")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="teaching_assignments")
    teacher = models.ForeignKey(
        "staff.StaffMember", on_delete=models.PROTECT, related_name="teaching_assignments"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.LECTURE)
    periods_per_week = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="How many lessons a week. The timetable generator fills up to this.",
    )
    is_active = models.BooleanField(
        default=True, db_index=True,
        help_text="False once the teacher no longer teaches it (e.g. after a hand-over). "
                  "Kept as a record; can't get new lessons.",
    )

    class Meta:
        db_table = "academics_teaching_assignment"
        ordering = ["section", "subject__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["section", "subject", "teacher", "role"], name="uniq_teaching_assignment_role"
            ),
        ]

    def __str__(self):
        return f"{self.teacher} → {self.subject.name}, {self.section.display_name}"


class StudentElectiveQuerySet(models.QuerySet):
    def on(self, day=None):
        """Choices in effect on ``day`` (default today): taken from
        ``started_on`` up to, not including, ``ended_on``."""
        from django.utils import timezone

        day = day or timezone.localdate()
        return self.filter(started_on__lte=day).filter(
            Q(ended_on__isnull=True) | Q(ended_on__gt=day),
            # ...and only while the student is in that class.
            Q(enrollment__ended_on__isnull=True) | Q(enrollment__ended_on__gt=day),
        )


class StudentElective(TimeStampedModel):
    """An elective subject a student takes in one class.

    Belongs to one enrollment (one stay in one section), so a student's
    choices in earlier classes stay in their history. Dropping a subject ends
    the choice rather than deleting it, so what they took, and when, stays
    true. Compulsory subjects aren't recorded: every student of the section
    takes them.
    """

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="student_electives"
    )
    enrollment = models.ForeignKey(
        "students.Enrollment", on_delete=models.CASCADE, related_name="electives"
    )
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="student_choices")
    started_on = models.DateField()
    ended_on = models.DateField(null=True, blank=True, help_text="Set when the subject is dropped.")

    objects = StudentElectiveQuerySet.as_manager()

    class Meta:
        db_table = "academics_student_elective"
        ordering = ["enrollment", "subject__name", "started_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["enrollment", "subject"], condition=Q(ended_on__isnull=True),
                name="uniq_open_student_elective",
            ),
            models.CheckConstraint(
                condition=Q(ended_on__isnull=True) | Q(ended_on__gt=F("started_on")),
                name="student_elective_dates_ordered",
            ),
        ]

    def __str__(self):
        return f"{self.enrollment.student} takes {self.subject.name}"


class CalendarEvent(OrganizationOwnedModel):
    """A day or span on the academic calendar: holidays (Dashain, Tihar),
    closures (strikes, snow days), exam days, events, and make-up days that
    run another weekday's timetable.

    Narrowed by ``campus`` (empty: every campus), ``program`` and ``level``
    (empty: all), so "Grade 10 SEE preparation exams at Lalitpur" is one
    event. The timetable reads it: suspended days show their lessons as
    cancelled, and attendance won't expect them.
    """

    class Kind(models.TextChoices):
        HOLIDAY = "holiday", "Holiday"
        CLOSURE = "closure", "Closure"
        EXAM = "exam", "Exam"
        EVENT = "event", "Event"
        MAKEUP_DAY = "makeup_day", "Make-up day"

    # Whether classes stop, unless said otherwise.
    SUSPENDS_BY_DEFAULT = {Kind.HOLIDAY: True, Kind.CLOSURE: True, Kind.EXAM: True,
                           Kind.EVENT: False, Kind.MAKEUP_DAY: False}

    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField(help_text="Inclusive. The same as start_date for one day.")
    campus = models.ForeignKey(
        "organizations.Campus", null=True, blank=True, on_delete=models.PROTECT,
        related_name="calendar_events", help_text="Empty: every campus.",
    )
    program = models.ForeignKey(
        Program, null=True, blank=True, on_delete=models.PROTECT, related_name="calendar_events",
        help_text="Empty: every program.",
    )
    level = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="With a program: only this grade or semester."
    )
    suspends_classes = models.BooleanField(help_text="Regular lessons don't take place.")
    runs_timetable_of = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Make-up day: run this weekday's timetable (1 = Monday … 7 = Sunday).",
    )

    class Meta:
        db_table = "academics_calendar_event"
        ordering = ["start_date", "pk"]
        indexes = [models.Index(fields=["organization", "start_date", "end_date"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")), name="calendar_event_dates_ordered"
            ),
            models.CheckConstraint(
                condition=(Q(kind="makeup_day", runs_timetable_of__gte=1, runs_timetable_of__lte=7,
                             suspends_classes=False)
                           | (~Q(kind="makeup_day") & Q(runs_timetable_of__isnull=True))),
                name="calendar_makeup_day_names_a_weekday",
            ),
            models.CheckConstraint(
                condition=Q(level__isnull=True) | Q(program__isnull=False),
                name="calendar_level_needs_program",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.start_date}–{self.end_date})"

    def applies_to(self, section) -> bool:
        return (
            self.campus_id in (None, section.campus_id)
            and self.program_id in (None, section.program_id)
            and self.level in (None, section.level)
        )
