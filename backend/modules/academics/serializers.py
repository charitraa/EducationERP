from rest_framework import serializers

from core.common.serializers import (
    ensure_unique_in_organization,
    ensure_unique_together,
    target_organization_id,
)
from core.organizations.models import Campus
from modules.staff.models import StaffMember
from modules.students.models import Enrollment, Student

from .models import (
    AcademicYear,
    Batch,
    CurriculumSubject,
    Department,
    Program,
    Room,
    Section,
    StudentElective,
    Subject,
    TeachingAssignment,
    Term,
)


class TenantSerializer(serializers.ModelSerializer):
    """Shared checks: every linked record must belong to the same organization.

    An id from another organization is answered exactly like a missing one,
    so nothing about other tenants is revealed.
    """

    def own(self, obj, label="record"):
        if obj is not None and obj.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return obj

    def current(self, attrs, field):
        """The value a field will have after this write."""
        if field in attrs:
            return attrs[field]
        return getattr(self.instance, field, None) if self.instance is not None else None


def _code(serializer, value):
    value = value.strip().lower()
    ensure_unique_in_organization(serializer, "code", value)
    return value


def _staff_field():
    return serializers.PrimaryKeyRelatedField(
        queryset=StaffMember.objects.all(), required=False, allow_null=True
    )


# ---------------------------------------------------------------------------
class DepartmentSerializer(TenantSerializer):
    head = _staff_field()
    head_name = serializers.CharField(source="head.full_name", read_only=True, default=None)

    class Meta:
        model = Department
        fields = ["id", "organization", "code", "name", "description", "head", "head_name",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        return _code(self, value)

    def validate_head(self, head):
        return self.own(head, "staff member")


class ProgramSerializer(TenantSerializer):
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(), required=False, allow_null=True
    )
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)

    class Meta:
        model = Program
        fields = ["id", "organization", "code", "name", "department", "department_name",
                  "level_type", "first_level", "last_level", "description", "is_active",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        return _code(self, value)

    def validate_department(self, department):
        return self.own(department, "department")

    def validate(self, attrs):
        first, last = self.current(attrs, "first_level"), self.current(attrs, "last_level")
        if first is not None and last is not None and last < first:
            raise serializers.ValidationError({"last_level": "Cannot be below the first level."})
        # Shrinking the range must not strand sections or curriculum outside it.
        if self.instance is not None and ("first_level" in attrs or "last_level" in attrs):
            outside = (
                self.instance.sections.exclude(level__range=(first, last)).exists()
                or self.instance.curriculum.exclude(level__range=(first, last)).exists()
            )
            if outside:
                raise serializers.ValidationError(
                    {"last_level": "Sections or curriculum exist at levels outside the new range."}
                )
        return attrs


class SubjectSerializer(TenantSerializer):
    department = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = Subject
        fields = ["id", "organization", "code", "name", "department", "credit_hours",
                  "description", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        return _code(self, value)

    def validate_department(self, department):
        return self.own(department, "department")

    def validate_credit_hours(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError("Must be more than zero.")
        return value


class CurriculumSubjectSerializer(TenantSerializer):
    program = serializers.PrimaryKeyRelatedField(queryset=Program.objects.all())
    subject = serializers.PrimaryKeyRelatedField(queryset=Subject.objects.all())
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    subject_code = serializers.CharField(source="subject.code", read_only=True)
    level_label = serializers.SerializerMethodField()

    class Meta:
        model = CurriculumSubject
        fields = ["id", "program", "level", "level_label", "subject", "subject_name",
                  "subject_code", "is_elective", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_level_label(self, entry) -> str:
        return entry.program.level_label(entry.level)

    def validate_program(self, program):
        return self.own(program, "program")

    def validate_subject(self, subject):
        return self.own(subject, "subject")

    def validate(self, attrs):
        program, level = self.current(attrs, "program"), self.current(attrs, "level")
        if not program.has_level(level):
            raise serializers.ValidationError(
                {"level": f"{program.name} runs from level {program.first_level} to {program.last_level}."}
            )
        ensure_unique_together(
            self, attrs, ["program", "level", "subject"],
            {"subject": "This subject is already in the curriculum at this level."},
        )
        return attrs


class AcademicYearSerializer(TenantSerializer):
    class Meta:
        model = AcademicYear
        fields = ["id", "organization", "name", "start_date", "end_date", "is_current",
                  "created_at", "updated_at"]
        # Switched only through the set-current action, which unsets the old one.
        read_only_fields = ["id", "organization", "is_current", "created_at", "updated_at"]

    def validate_name(self, value):
        value = value.strip()
        ensure_unique_in_organization(self, "name", value)
        return value

    def validate(self, attrs):
        start, end = self.current(attrs, "start_date"), self.current(attrs, "end_date")
        if end <= start:
            raise serializers.ValidationError({"end_date": "Must be after the start date."})
        overlapping = AcademicYear.objects.filter(
            organization_id=target_organization_id(self), start_date__lte=end, end_date__gte=start
        )
        if self.instance is not None:
            overlapping = overlapping.exclude(pk=self.instance.pk)
            if self.instance.terms.exclude(start_date__gte=start, end_date__lte=end).exists():
                raise serializers.ValidationError("Some terms would fall outside the new dates.")
        if overlapping.exists():
            raise serializers.ValidationError(
                f"Overlaps the academic year {overlapping.first().name}."
            )
        return attrs


class TermSerializer(TenantSerializer):
    academic_year = serializers.PrimaryKeyRelatedField(queryset=AcademicYear.objects.all())
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)

    class Meta:
        model = Term
        fields = ["id", "academic_year", "academic_year_name", "name", "sequence",
                  "start_date", "end_date", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_academic_year(self, year):
        self.own(year, "academic year")
        if self.instance is not None and year.pk != self.instance.academic_year_id:
            raise serializers.ValidationError("A term cannot move to another academic year.")
        return year

    def validate(self, attrs):
        year = self.current(attrs, "academic_year")
        start, end = self.current(attrs, "start_date"), self.current(attrs, "end_date")
        if end <= start:
            raise serializers.ValidationError({"end_date": "Must be after the start date."})
        if start < year.start_date or end > year.end_date:
            raise serializers.ValidationError(
                f"Must fall within {year.name} ({year.start_date} to {year.end_date})."
            )
        others = Term.objects.filter(academic_year=year)
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        clash = others.filter(start_date__lte=end, end_date__gte=start).first()
        if clash is not None:
            raise serializers.ValidationError(f"Overlaps {clash.name}.")
        ensure_unique_together(
            self, attrs, ["academic_year", "sequence"],
            {"sequence": "Another term of this year already has this number."},
        )
        return attrs


class BatchSerializer(TenantSerializer):
    program = serializers.PrimaryKeyRelatedField(queryset=Program.objects.all())
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    start_year = serializers.PrimaryKeyRelatedField(queryset=AcademicYear.objects.all())
    program_name = serializers.CharField(source="program.name", read_only=True)
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    start_year_name = serializers.CharField(source="start_year.name", read_only=True)

    class Meta:
        model = Batch
        fields = ["id", "organization", "code", "name", "program", "program_name", "campus",
                  "campus_name", "start_year", "start_year_name", "is_active",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        return _code(self, value)

    def validate_program(self, program):
        return self.own(program, "program")

    def validate_campus(self, campus):
        return self.own(campus, "campus")

    def validate_start_year(self, year):
        return self.own(year, "academic year")

    def validate(self, attrs):
        if self.instance is not None and self.instance.sections.exists():
            for field in ("program", "campus"):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError(
                        {field: "Cannot change: the batch already has sections."}
                    )
        return attrs


class RoomSerializer(TenantSerializer):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    campus_name = serializers.CharField(source="campus.name", read_only=True)

    class Meta:
        model = Room
        fields = ["id", "organization", "campus", "campus_name", "code", "name", "building",
                  "floor", "room_type", "capacity", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_campus(self, campus):
        self.own(campus, "campus")
        if self.instance is not None and campus.pk != self.instance.campus_id:
            raise serializers.ValidationError("A room cannot move to another campus.")
        return campus

    def validate(self, attrs):
        if "code" in attrs:
            attrs["code"] = attrs["code"].strip().lower()
        ensure_unique_together(
            self, attrs, ["campus", "code"], {"code": "This campus already has a room with this code."}
        )
        return attrs


class SectionSerializer(TenantSerializer):
    academic_year = serializers.PrimaryKeyRelatedField(queryset=AcademicYear.objects.all())
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    program = serializers.PrimaryKeyRelatedField(queryset=Program.objects.all())
    batch = serializers.PrimaryKeyRelatedField(
        queryset=Batch.objects.all(), required=False, allow_null=True
    )
    class_teacher = _staff_field()
    home_room = serializers.PrimaryKeyRelatedField(
        queryset=Room.objects.all(), required=False, allow_null=True
    )
    display_name = serializers.CharField(read_only=True)
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)
    campus_name = serializers.CharField(source="campus.name", read_only=True)
    program_name = serializers.CharField(source="program.name", read_only=True)
    class_teacher_name = serializers.CharField(source="class_teacher.full_name", read_only=True, default=None)
    student_count = serializers.SerializerMethodField()

    class Meta:
        model = Section
        fields = ["id", "organization", "display_name", "academic_year", "academic_year_name",
                  "campus", "campus_name", "program", "program_name", "level", "name", "batch",
                  "class_teacher", "class_teacher_name", "home_room", "capacity",
                  "student_count", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def get_student_count(self, section) -> int:
        # Lists annotate the count in one query; a freshly created section
        # isn't annotated, so count directly.
        count = getattr(section, "student_count", None)
        if count is None:
            count = section.enrollments.filter(status="active").count()
        return count

    def validate_academic_year(self, year):
        return self.own(year, "academic year")

    def validate_campus(self, campus):
        return self.own(campus, "campus")

    def validate_program(self, program):
        return self.own(program, "program")

    def validate_batch(self, batch):
        return self.own(batch, "batch")

    def validate_class_teacher(self, teacher):
        self.own(teacher, "staff member")
        if teacher is not None and teacher.status == StaffMember.Status.LEFT:
            raise serializers.ValidationError("This staff member has left.")
        return teacher

    def validate_home_room(self, room):
        return self.own(room, "room")

    def validate(self, attrs):
        get = lambda field: self.current(attrs, field)  # noqa: E731
        program, level, campus = get("program"), get("level"), get("campus")

        if self.instance is not None:
            # Students are placed by these; moving the section underneath
            # them would silently change their history.
            for field in ("academic_year", "campus", "program", "level"):
                if field in attrs and attrs[field] != getattr(self.instance, field) \
                        and self.instance.enrollments.exists():
                    raise serializers.ValidationError(
                        {field: "Cannot change: students have been placed in this section."}
                    )
            # Its lessons use that campus's periods and rooms.
            for field in ("academic_year", "campus"):
                if field in attrs and attrs[field] != getattr(self.instance, field) \
                        and self.instance.teaching_assignments.filter(
                            timetable_entries__isnull=False,
                            timetable_entries__deleted_at__isnull=True,
                        ).exists():
                    raise serializers.ValidationError(
                        {field: "Cannot change: this section has lessons on the timetable."}
                    )

        if not program.has_level(level):
            raise serializers.ValidationError(
                {"level": f"{program.name} runs from level {program.first_level} to {program.last_level}."}
            )
        batch = get("batch")
        if batch is not None and (batch.program_id != program.pk or batch.campus_id != campus.pk):
            raise serializers.ValidationError({"batch": "The batch belongs to another program or campus."})
        room = get("home_room")
        if room is not None and room.campus_id != campus.pk:
            raise serializers.ValidationError({"home_room": "The room is at another campus."})
        teacher = get("class_teacher")
        if teacher is not None and teacher.campus_id != campus.pk:
            # A class teacher is the section's day-to-day contact, so they must
            # work at its campus. (Subject teachers may teach anywhere.)
            raise serializers.ValidationError({"class_teacher": "The teacher works at another campus."})

        if "name" in attrs:
            attrs["name"] = attrs["name"].strip()
        ensure_unique_together(
            self, attrs, ["academic_year", "campus", "program", "level", "name"],
            {"name": "This section already exists for that year, campus, program and level."},
        )
        return attrs


class TeachingAssignmentSerializer(TenantSerializer):
    section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.all())
    subject = serializers.PrimaryKeyRelatedField(queryset=Subject.objects.all())
    teacher = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.all())
    section_name = serializers.CharField(source="section.display_name", read_only=True)
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    teacher_name = serializers.CharField(source="teacher.full_name", read_only=True)

    class Meta:
        model = TeachingAssignment
        fields = ["id", "section", "section_name", "subject", "subject_name", "teacher",
                  "teacher_name", "periods_per_week", "created_at"]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"periods_per_week": {"min_value": 1, "max_value": 60}}

    def validate_section(self, section):
        return self.own(section, "section")

    def validate_subject(self, subject):
        return self.own(subject, "subject")

    def validate_teacher(self, teacher):
        self.own(teacher, "staff member")
        if teacher.status == StaffMember.Status.LEFT:
            raise serializers.ValidationError("This staff member has left.")
        return teacher

    def validate(self, attrs):
        section, subject = self.current(attrs, "section"), self.current(attrs, "subject")
        # Keeps the timetable, attendance and marks tied to the syllabus.
        if not CurriculumSubject.objects.filter(
            program_id=section.program_id, level=section.level, subject=subject
        ).exists():
            raise serializers.ValidationError(
                {"subject": f"{subject.name} is not in the curriculum for {section.display_name} "
                            f"of {section.program.name}. Add it to the curriculum first."}
            )
        ensure_unique_together(
            self, attrs, ["section", "subject", "teacher"],
            {"teacher": "This teacher already teaches this subject to this section."},
        )
        return attrs


class SectionStudentSerializer(serializers.Serializer):
    """A student as listed in a section."""

    id = serializers.IntegerField()
    student_number = serializers.CharField()
    full_name = serializers.CharField()
    status = serializers.CharField()


class StudentElectiveSerializer(TenantSerializer):
    """Record that a student takes an elective in the class they're in now.

    Written with ``student``; stored against their open enrollment, so the
    choice stays with that class in their history."""

    student = serializers.PrimaryKeyRelatedField(
        queryset=Student.objects.all(), write_only=True, help_text="Uses the student's current class."
    )
    subject = serializers.PrimaryKeyRelatedField(queryset=Subject.objects.all())
    student_id = serializers.IntegerField(source="enrollment.student_id", read_only=True)
    student_name = serializers.CharField(source="enrollment.student.full_name", read_only=True)
    student_number = serializers.CharField(source="enrollment.student.student_number", read_only=True)
    section = serializers.IntegerField(source="enrollment.section_id", read_only=True)
    section_name = serializers.CharField(source="enrollment.section.display_name", read_only=True)
    subject_name = serializers.CharField(source="subject.name", read_only=True)

    class Meta:
        model = StudentElective
        fields = ["id", "student", "student_id", "student_name", "student_number", "enrollment",
                  "section", "section_name", "subject", "subject_name", "created_at"]
        read_only_fields = ["id", "enrollment", "created_at"]

    def validate_student(self, student):
        return self.own(student, "student")

    def validate_subject(self, subject):
        return self.own(subject, "subject")

    def validate(self, attrs):
        enrollment = (
            attrs.pop("student").enrollments.filter(status=Enrollment.Status.ACTIVE)
            .select_related("section__program").first()
        )
        if enrollment is None or enrollment.section is None:
            raise serializers.ValidationError({"student": "The student isn't placed in a class."})
        section, subject = enrollment.section, attrs["subject"]
        if not CurriculumSubject.objects.filter(
            program_id=section.program_id, level=section.level, subject=subject, is_elective=True
        ).exists():
            raise serializers.ValidationError(
                {"subject": f"{subject.name} is not an elective for {section.display_name} "
                            f"of {section.program.name}."}
            )
        if enrollment.electives.filter(subject=subject).exists():
            raise serializers.ValidationError({"subject": "The student already takes this subject."})

        taken = list(enrollment.electives.values_list("subject_id", flat=True))
        clash = _parallel_lesson(section, subject.pk, taken)
        if clash is not None:
            raise serializers.ValidationError(
                {"subject": f"{subject.name} is taught at the same time as {clash}, "
                            f"which the student already takes."}
            )
        attrs["enrollment"] = enrollment
        return attrs


def _parallel_lesson(section, subject_id, other_subject_ids):
    """The name of another of these subjects whose lessons in ``section``
    meet at the same time as ``subject_id``'s, if any.

    Reads the timetable through ``TeachingAssignment.timetable_entries`` so
    academics doesn't import the timetable module that builds on it."""
    if not other_subject_ids:
        return None
    rows = list(
        TeachingAssignment.objects.filter(
            section=section, subject_id__in=[subject_id, *other_subject_ids],
            timetable_entries__isnull=False, timetable_entries__deleted_at__isnull=True,
        ).values(
            "subject_id", "subject__name", "timetable_entries__day_of_week",
            "timetable_entries__term_id", "timetable_entries__period__start_time",
            "timetable_entries__period__end_time",
        )
    )
    mine = [r for r in rows if r["subject_id"] == subject_id]
    others = [r for r in rows if r["subject_id"] != subject_id]
    for a in mine:
        for b in others:
            same_day = a["timetable_entries__day_of_week"] == b["timetable_entries__day_of_week"]
            terms = (a["timetable_entries__term_id"], b["timetable_entries__term_id"])
            same_term = None in terms or terms[0] == terms[1]
            overlap = (a["timetable_entries__period__start_time"] < b["timetable_entries__period__end_time"]
                       and b["timetable_entries__period__start_time"] < a["timetable_entries__period__end_time"])
            if same_day and same_term and overlap:
                return b["subject__name"]
    return None


class PromoteSectionSerializer(serializers.Serializer):
    to_section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.all())
    exclude = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list,
        help_text="Ids of students who stay behind (held back, or placed separately).",
    )
    on_date = serializers.DateField(required=False, help_text="When the move takes effect. Default: today.")
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")

    def validate_to_section(self, section):
        source = self.context["section"]
        if section.organization_id != source.organization_id:
            raise serializers.ValidationError("Unknown section.")
        if section.pk == source.pk:
            raise serializers.ValidationError("Choose another section.")
        if section.campus_id != source.campus_id:
            raise serializers.ValidationError("The section is at another campus.")
        return section
