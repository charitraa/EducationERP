from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from core.common.exceptions import ConflictError
from core.common.serializers import ensure_unique_in_organization, target_organization_id
from modules.academics.models import CurriculumSubject, Section

from . import results as results_service, services
from .models import (
    AdmitCard,
    DivisionBand,
    Exam,
    ExamComponent,
    ExamRoom,
    ExamSubject,
    ExamType,
    GradeBand,
    GradeScale,
    Invigilation,
    Mark,
    MarkCorrection,
    MarkSheet,
    MarkStatus,
    Result,
    ResultPlan,
    ResultPlanExam,
    SeatAllocation,
    SubjectResult,
)


class OwnedModelSerializer(serializers.ModelSerializer):
    def own(self, obj, label):
        """Another organization's id is answered like a missing one."""
        if obj is not None and obj.organization_id != target_organization_id(self):
            raise serializers.ValidationError(f"Unknown {label}.")
        return obj


# ---------------------------------------------------------------------------
# Grade scales
# ---------------------------------------------------------------------------
class GradeBandSerializer(serializers.ModelSerializer):
    class Meta:
        model = GradeBand
        fields = ["min_percentage", "letter", "grade_point", "remark", "is_pass"]


class DivisionBandSerializer(serializers.ModelSerializer):
    class Meta:
        model = DivisionBand
        fields = ["min_percentage", "name"]


class GradeScaleSerializer(OwnedModelSerializer):
    bands = GradeBandSerializer(many=True, required=False)
    divisions = DivisionBandSerializer(many=True, required=False)
    preset = serializers.ChoiceField(
        choices=list(services.PRESETS), write_only=True, required=False,
        help_text="Start from a ready-made table, then edit the bands. Ignored when bands are given.")
    in_use = serializers.SerializerMethodField()
    program_name = serializers.CharField(source="program.name", read_only=True, default=None)

    class Meta:
        model = GradeScale
        fields = ["id", "organization", "program", "program_name", "name", "max_grade_point",
                  "require_all_subjects_pass", "overall_pass_percentage", "bands", "divisions", "preset",
                  "in_use", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def get_in_use(self, obj) -> bool:
        return services.scale_in_use(obj)

    def validate_program(self, program):
        return self.own(program, "program")

    def validate(self, attrs):
        program = attrs.get("program", self.instance.program if self.instance else None)
        clash = GradeScale.objects.filter(organization_id=target_organization_id(self), program=program)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                {"program": "This program already has a grade scale." if program else
                 "The organization already has a default grade scale."})
        if self.instance is None and not attrs.get("bands") and not attrs.get("preset"):
            raise serializers.ValidationError({"bands": "Give the grade bands, or choose a preset."})
        return attrs

    def _bands(self, validated):
        bands, divisions = validated.pop("bands", None), validated.pop("divisions", None)
        preset = validated.pop("preset", None)
        if bands is None and preset:
            bands, divisions = services.preset_bands(preset)
        return ([dict(b) for b in bands] if bands is not None else None,
                [dict(d) for d in (divisions or [])])

    @transaction.atomic
    def create(self, validated_data):
        bands, divisions = self._bands(validated_data)
        scale = super().create(validated_data)
        services.replace_bands(scale, bands, divisions)
        return scale

    @transaction.atomic
    def update(self, instance, validated_data):
        given_bands = "bands" in validated_data
        bands, divisions = self._bands(validated_data)
        if not given_bands and "divisions" in self.initial_data:
            bands = [{"min_percentage": b.min_percentage, "letter": b.letter, "grade_point": b.grade_point,
                      "remark": b.remark, "is_pass": b.is_pass} for b in instance.bands.all()]
            given_bands = True
        scale = super().update(instance, validated_data)
        if given_bands:
            services.replace_bands(scale, bands, divisions)
        return scale


class ExamTypeSerializer(OwnedModelSerializer):
    class Meta:
        model = ExamType
        fields = ["id", "organization", "code", "name", "description", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_code(self, value):
        value = value.lower()
        ensure_unique_in_organization(self, "code", value)
        return value


# ---------------------------------------------------------------------------
# Exams
# ---------------------------------------------------------------------------
class ExamSerializer(OwnedModelSerializer):
    grade_scale = serializers.PrimaryKeyRelatedField(
        queryset=GradeScale.objects.all(), required=False,
        help_text="Default: the program's scale, else the organization's default.")
    levels = serializers.SerializerMethodField()
    exam_type_name = serializers.CharField(source="exam_type.name", read_only=True)
    program_name = serializers.CharField(source="program.name", read_only=True)

    class Meta:
        model = Exam
        fields = ["id", "organization", "campus", "academic_year", "term", "exam_type", "exam_type_name",
                  "program", "program_name", "name", "grade_scale", "status", "start_date", "end_date",
                  "levels", "min_attendance_percent", "instructions", "on_transcript", "published_at",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "status", "start_date", "end_date", "published_at",
                            "created_at", "updated_at"]

    def get_levels(self, obj) -> list[int]:
        return services.exam_levels(obj)

    def validate_campus(self, value):
        return self.own(value, "campus")

    def validate_academic_year(self, value):
        return self.own(value, "academic year")

    def validate_term(self, value):
        return self.own(value, "term")

    def validate_exam_type(self, value):
        return self.own(value, "exam type")

    def validate_program(self, value):
        return self.own(value, "program")

    def validate_grade_scale(self, value):
        return self.own(value, "grade scale")

    def validate(self, attrs):
        instance = self.instance
        get = lambda k: attrs.get(k, getattr(instance, k, None) if instance else None)
        term, year = get("term"), get("academic_year")
        if term is not None and term.academic_year_id != year.pk:
            raise serializers.ValidationError({"term": "This term belongs to a different academic year."})
        if instance is not None:
            if instance.status == Exam.Status.PUBLISHED:
                raise ConflictError("The results are published, so the exam can't be changed.",
                                    code="exam_published")
            structural = {"campus", "academic_year", "program"} & set(attrs)
            if any(attrs[f] != getattr(instance, f) for f in structural) and instance.subjects.exists():
                raise serializers.ValidationError("Papers are already set up; the campus, year and "
                                                  "program can't change.")
        clash = Exam.objects.filter(campus=get("campus"), academic_year=year, name=get("name"))
        if instance is not None:
            clash = clash.exclude(pk=instance.pk)
        if clash.exists():
            raise serializers.ValidationError({"name": "An exam with this name already exists for the year."})
        program, scale = get("program"), attrs.get("grade_scale")
        if scale is not None and scale.program_id not in (None, program.pk):
            raise serializers.ValidationError({"grade_scale": "This scale belongs to another program."})
        if scale is None and (instance is None or "program" in attrs):
            attrs["grade_scale"] = services.resolve_scale(target_organization_id(self), program)
        return attrs


class ExamComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamComponent
        fields = ["id", "kind", "name", "full_marks", "pass_marks", "order"]

    def validate(self, attrs):
        full, pass_marks = attrs.get("full_marks"), attrs.get("pass_marks", Decimal("0"))
        if full is not None and (full <= 0 or pass_marks < 0 or pass_marks > full):
            raise serializers.ValidationError("Full marks must be above 0, and the pass mark within them.")
        return attrs


class ExamSubjectSerializer(OwnedModelSerializer):
    components = ExamComponentSerializer(many=True, required=False)
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    full_marks = serializers.SerializerMethodField()

    class Meta:
        model = ExamSubject
        fields = ["id", "organization", "exam", "subject", "subject_name", "level", "date", "start_time",
                  "end_time", "credit_hours", "components", "full_marks", "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def get_full_marks(self, obj) -> Decimal:
        return sum((c.full_marks for c in obj.components.all()), Decimal("0"))

    def validate_exam(self, value):
        return self.own(value, "exam")

    def validate_subject(self, value):
        return self.own(value, "subject")

    def validate(self, attrs):
        instance = self.instance
        exam = attrs.get("exam", instance.exam if instance else None)
        subject = attrs.get("subject", instance.subject if instance else None)
        level = attrs.get("level", instance.level if instance else None)
        if instance is not None and exam != instance.exam:
            raise serializers.ValidationError({"exam": "A paper can't move to another exam."})
        services.ensure_open_for_structure(exam)
        if not exam.program.has_level(level):
            raise serializers.ValidationError({"level": f"{exam.program.name} has no such level."})
        if not CurriculumSubject.objects.filter(program=exam.program, level=level, subject=subject).exists():
            raise serializers.ValidationError({"subject": f"{subject.name} isn't taught at this level of "
                                                          f"{exam.program.name}."})
        if instance is None and not attrs.get("components"):
            raise serializers.ValidationError({"components": "Give at least one marks component."})
        if instance is not None and "components" in attrs:
            services.ensure_no_marks(instance)
        names = [c["name"] for c in attrs.get("components", [])]
        if len(names) != len(set(names)):
            raise serializers.ValidationError({"components": "Two components have the same name."})
        clash = ExamSubject.objects.filter(exam=exam, subject=subject, level=level)
        if instance is not None:
            clash = clash.exclude(pk=instance.pk)
        if clash.exists():
            raise serializers.ValidationError("This subject already has a paper at this level.")
        start, end = attrs.get("start_time"), attrs.get("end_time")
        if start and end and end <= start:
            raise serializers.ValidationError({"end_time": "The paper must end after it starts."})
        return attrs

    def _check(self, paper):
        services.check_paper(paper)
        services.reschedule_paper(paper)

    @transaction.atomic
    def create(self, validated_data):
        components = validated_data.pop("components")
        paper = ExamSubject.objects.create(**validated_data)
        ExamComponent.objects.bulk_create([
            ExamComponent(organization_id=paper.organization_id, exam_subject=paper, **c) for c in components])
        self._check(paper)
        return paper

    @transaction.atomic
    def update(self, instance, validated_data):
        components = validated_data.pop("components", None)
        paper = super().update(instance, validated_data)
        if components is not None:
            paper.components.all().delete()
            ExamComponent.objects.bulk_create([
                ExamComponent(organization_id=paper.organization_id, exam_subject=paper, **c) for c in components])
        self._check(paper)
        return paper


class ExamRoomSerializer(OwnedModelSerializer):
    room_name = serializers.CharField(source="room.name", read_only=True)
    seats = serializers.IntegerField(source="seat_count", read_only=True)

    class Meta:
        model = ExamRoom
        fields = ["id", "organization", "exam", "room", "room_name", "capacity", "seats", "created_at"]
        read_only_fields = ["id", "organization", "created_at"]

    def validate_exam(self, value):
        return self.own(value, "exam")

    def validate_room(self, value):
        return self.own(value, "room")

    def validate(self, attrs):
        exam = attrs.get("exam", self.instance.exam if self.instance else None)
        room = attrs.get("room", self.instance.room if self.instance else None)
        if room.campus_id != exam.campus_id:
            raise serializers.ValidationError({"room": "This room is at a different campus."})
        services.ensure_open_for_structure(exam)
        clash = ExamRoom.objects.filter(exam=exam, room=room)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({"room": "This room is already used for the exam."})
        return attrs


class SeatAllocationSerializer(serializers.ModelSerializer):
    student = serializers.IntegerField(source="enrollment.student_id", read_only=True)
    student_name = serializers.CharField(source="enrollment.student.full_name", read_only=True)
    section_name = serializers.CharField(source="enrollment.section.display_name", read_only=True)
    room_name = serializers.CharField(source="exam_room.room.name", read_only=True)

    class Meta:
        model = SeatAllocation
        fields = ["id", "exam", "enrollment", "student", "student_name", "section_name", "exam_room",
                  "room_name", "seat_number"]
        read_only_fields = fields


class InvigilationSerializer(OwnedModelSerializer):
    staff_name = serializers.CharField(source="staff.full_name", read_only=True)
    subject_name = serializers.CharField(source="exam_subject.subject.name", read_only=True)
    room_name = serializers.CharField(source="exam_room.room.name", read_only=True)

    class Meta:
        model = Invigilation
        fields = ["id", "organization", "exam_subject", "subject_name", "exam_room", "room_name", "staff",
                  "staff_name", "is_chief", "created_at"]
        read_only_fields = ["id", "organization", "created_at"]

    def validate_exam_subject(self, value):
        return self.own(value, "paper")

    def validate_exam_room(self, value):
        return self.own(value, "room")

    def validate_staff(self, value):
        return self.own(value, "staff member")

    def validate(self, attrs):
        paper = attrs.get("exam_subject", self.instance.exam_subject if self.instance else None)
        exam_room = attrs.get("exam_room", self.instance.exam_room if self.instance else None)
        staff = attrs.get("staff", self.instance.staff if self.instance else None)
        if exam_room.exam_id != paper.exam_id:
            raise serializers.ValidationError({"exam_room": "This room isn't used for the paper's exam."})
        services.ensure_open_for_structure(paper.exam)
        clash = Invigilation.objects.filter(exam_subject=paper, exam_room=exam_room, staff=staff)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("This person is already assigned to this room for the paper.")
        services.check_invigilator(paper, staff, exclude_pk=self.instance.pk if self.instance else None)
        return attrs


class AdmitCardSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    section_name = serializers.CharField(source="enrollment.section.display_name", read_only=True)

    class Meta:
        model = AdmitCard
        fields = ["id", "exam", "enrollment", "student", "student_name", "student_number", "section_name",
                  "card_number", "status", "withheld_reason", "created_at"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------
class MarkSheetSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source="exam_subject.subject.name", read_only=True)
    exam = serializers.IntegerField(source="exam_subject.exam_id", read_only=True)
    exam_name = serializers.CharField(source="exam_subject.exam.name", read_only=True)
    section_name = serializers.CharField(source="section.display_name", read_only=True)
    date = serializers.DateField(source="exam_subject.date", read_only=True)

    class Meta:
        model = MarkSheet
        fields = ["id", "exam", "exam_name", "exam_subject", "subject_name", "date", "section", "section_name",
                  "status", "submitted_at", "submitted_by", "verified_at", "verified_by", "review_note",
                  "created_at"]
        read_only_fields = fields


class OpenSheetSerializer(serializers.Serializer):
    exam_subject = serializers.PrimaryKeyRelatedField(queryset=ExamSubject.objects.select_related(
        "exam__academic_year", "exam__organization", "subject"))
    section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.select_related("program", "campus"))

    def validate_exam_subject(self, value):
        if value.organization_id != self.context["request"].user.organization_id:
            raise serializers.ValidationError("Unknown paper.")
        return value

    def validate_section(self, value):
        if value.organization_id != self.context["request"].user.organization_id:
            raise serializers.ValidationError("Unknown class.")
        return value


class MarkEntrySerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    component = serializers.IntegerField()
    status = serializers.ChoiceField(choices=MarkStatus.choices, default=MarkStatus.PRESENT)
    marks = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)


class EnterMarksSerializer(serializers.Serializer):
    entries = MarkEntrySerializer(many=True, allow_empty=False)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255, default="",
                                   help_text="Required once the sheet is submitted or verified.")


class MarkCorrectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarkCorrection
        fields = ["old_status", "old_marks", "new_status", "new_marks", "reason", "corrected_by", "corrected_at"]
        read_only_fields = fields


class MarkSerializer(serializers.ModelSerializer):
    component_name = serializers.CharField(source="component.name", read_only=True)
    full_marks = serializers.DecimalField(source="component.full_marks", max_digits=6, decimal_places=2,
                                          read_only=True)
    student = serializers.IntegerField(source="enrollment.student_id", read_only=True)
    student_name = serializers.CharField(source="enrollment.student.full_name", read_only=True)
    corrections = MarkCorrectionSerializer(many=True, read_only=True)

    class Meta:
        model = Mark
        fields = ["id", "sheet", "enrollment", "student", "student_name", "component", "component_name",
                  "full_marks", "status", "marks", "entered_by", "corrections", "updated_at"]
        read_only_fields = fields


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class CorrectMarkSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=MarkStatus.choices, default=MarkStatus.PRESENT)
    marks = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    reason = serializers.CharField(max_length=255)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
class SubjectResultSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source="subject.name", read_only=True)

    class Meta:
        model = SubjectResult
        fields = ["subject", "subject_name", "credit_hours", "obtained", "full", "percentage", "letter",
                  "grade_point", "remark", "status", "absent", "detail"]
        read_only_fields = fields


class ResultSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    section_name = serializers.CharField(source="section.display_name", read_only=True)
    published = serializers.BooleanField(source="is_published", read_only=True)
    subjects = SubjectResultSerializer(many=True, read_only=True)

    class Meta:
        model = Result
        fields = ["id", "exam", "plan", "student", "student_name", "student_number", "enrollment", "section",
                  "section_name", "total_obtained", "total_full", "percentage", "grade_point", "letter",
                  "division", "status", "rank_in_section", "rank_in_level", "remark", "published", "subjects",
                  "updated_at"]
        read_only_fields = fields


class ResultListSerializer(ResultSerializer):
    class Meta(ResultSerializer.Meta):
        fields = [f for f in ResultSerializer.Meta.fields if f != "subjects"]
        read_only_fields = fields


class RemarkSerializer(serializers.Serializer):
    remark = serializers.CharField(max_length=500, allow_blank=True)


class ResultPlanExamSerializer(serializers.ModelSerializer):
    exam_name = serializers.CharField(source="exam.name", read_only=True)

    class Meta:
        model = ResultPlanExam
        fields = ["exam", "exam_name", "weight"]


class ResultPlanSerializer(OwnedModelSerializer):
    items = ResultPlanExamSerializer(many=True, required=False)
    grade_scale = serializers.PrimaryKeyRelatedField(queryset=GradeScale.objects.all(), required=False)
    weights_total = serializers.SerializerMethodField()

    class Meta:
        model = ResultPlan
        fields = ["id", "organization", "campus", "academic_year", "term", "program", "name", "grade_scale",
                  "status", "on_transcript", "items", "weights_total", "published_at", "created_at",
                  "updated_at"]
        read_only_fields = ["id", "organization", "status", "published_at", "created_at", "updated_at"]

    def get_weights_total(self, obj) -> Decimal:
        return results_service.weights_total(obj)

    def validate_campus(self, value):
        return self.own(value, "campus")

    def validate_academic_year(self, value):
        return self.own(value, "academic year")

    def validate_term(self, value):
        return self.own(value, "term")

    def validate_program(self, value):
        return self.own(value, "program")

    def validate_grade_scale(self, value):
        return self.own(value, "grade scale")

    def validate_items(self, items):
        for item in items:
            self.own(item["exam"], "exam")
        return items

    def validate(self, attrs):
        instance = self.instance
        get = lambda k: attrs.get(k, getattr(instance, k, None) if instance else None)
        if instance is not None and instance.is_published:
            raise ConflictError("It's published. Unpublish it to change it.", code="plan_published")
        term, year = get("term"), get("academic_year")
        if term is not None and term.academic_year_id != year.pk:
            raise serializers.ValidationError({"term": "This term belongs to a different academic year."})
        campus, program = get("campus"), get("program")
        items = attrs.get("items", [])
        seen = set()
        for item in items:
            exam = item["exam"]
            if (exam.campus_id, exam.academic_year_id, exam.program_id) != (campus.pk, year.pk, program.pk):
                raise serializers.ValidationError(
                    {"items": f"{exam.name} is for a different campus, year or program."})
            if exam.pk in seen:
                raise serializers.ValidationError({"items": f"{exam.name} is listed twice."})
            seen.add(exam.pk)
        if sum((i["weight"] for i in items), Decimal("0")) > 100:
            raise serializers.ValidationError({"items": "The weights add up to more than 100%."})
        clash = ResultPlan.objects.filter(campus=campus, academic_year=year, name=get("name"))
        if instance is not None:
            clash = clash.exclude(pk=instance.pk)
        if clash.exists():
            raise serializers.ValidationError({"name": "A term result with this name already exists."})
        scale = attrs.get("grade_scale")
        if scale is not None and scale.program_id not in (None, program.pk):
            raise serializers.ValidationError({"grade_scale": "This scale belongs to another program."})
        if scale is None and (instance is None or "program" in attrs):
            attrs["grade_scale"] = services.resolve_scale(target_organization_id(self), program)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("items", [])
        plan = super().create(validated_data)
        ResultPlanExam.objects.bulk_create([ResultPlanExam(plan=plan, **i) for i in items])
        return plan

    @transaction.atomic
    def update(self, instance, validated_data):
        items = validated_data.pop("items", None)
        plan = super().update(instance, validated_data)
        if items is not None:
            plan.items.all().delete()
            ResultPlanExam.objects.bulk_create([ResultPlanExam(plan=plan, **i) for i in items])
        return plan


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
class AddCurriculumSerializer(serializers.Serializer):
    levels = serializers.ListField(child=serializers.IntegerField(min_value=0), allow_empty=False)
    full_marks = serializers.DecimalField(max_digits=6, decimal_places=2, default=Decimal("100"), min_value=1)
    pass_marks = serializers.DecimalField(max_digits=6, decimal_places=2, default=Decimal("35"), min_value=0)
    kind = serializers.ChoiceField(choices=ExamComponent.Kind.choices, default=ExamComponent.Kind.THEORY)

    def validate(self, attrs):
        if attrs["pass_marks"] > attrs["full_marks"]:
            raise serializers.ValidationError({"pass_marks": "Can't be above the full marks."})
        return attrs


class SeatPlanSerializer(serializers.Serializer):
    strategy = serializers.ChoiceField(choices=["interleave", "sequential"], default="interleave")
    dry_run = serializers.BooleanField(default=False)


class GenerateAdmitCardsSerializer(serializers.Serializer):
    section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.all(), required=False)


class WithholdSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)
