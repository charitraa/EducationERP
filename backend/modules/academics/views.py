from django.db import transaction
from django.db.models import Count, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError
from core.common.mixins import CampusScopedViewSet, OrganizationScopedViewSet
from modules.students.models import Enrollment
from modules.students.selectors import students_in_section
from modules.students.services import promote_section

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
from .selectors import students_taking
from .serializers import (
    AcademicYearSerializer,
    BatchSerializer,
    CurriculumSubjectSerializer,
    DepartmentSerializer,
    ProgramSerializer,
    PromoteSectionSerializer,
    RoomSerializer,
    SectionSerializer,
    SectionStudentSerializer,
    StudentElectiveSerializer,
    SubjectSerializer,
    TeachingAssignmentSerializer,
    TermSerializer,
)

VIEW = "academics.view"
STRUCTURE = "academics.manage_structure"
CLASSES = "academics.manage_classes"


def _perms(write: str, **extra) -> dict:
    return {
        "list": [VIEW], "retrieve": [VIEW],
        "create": [write], "update": [write], "partial_update": [write], "destroy": [write],
        **extra,
    }


def _schema(tag: str, noun: str):
    return extend_schema_view(
        list=extend_schema(tags=[tag], summary=f"List {noun}s"),
        retrieve=extend_schema(tags=[tag], summary=f"Retrieve a {noun}"),
        create=extend_schema(tags=[tag], summary=f"Create a {noun}"),
        update=extend_schema(tags=[tag], summary=f"Replace a {noun}"),
        partial_update=extend_schema(tags=[tag], summary=f"Update a {noun}"),
        destroy=extend_schema(tags=[tag], summary=f"Delete a {noun}"),
    )


class InUseProtectionMixin:
    """Refuse to delete a record other records still depend on.

    Most of these are soft-deleted, which never trips the database's PROTECT,
    so the check is explicit: ``in_use`` maps a related name to how the
    refusal is explained.
    """

    in_use: dict[str, str] = {}

    def perform_destroy(self, instance):
        for relation, what in self.in_use.items():
            if getattr(instance, relation).exists():
                raise ConflictError(f"Still in use by {what}. Remove those first.", code="in_use")
        super().perform_destroy(instance)


class AcademicsViewSet(InUseProtectionMixin, OrganizationScopedViewSet):
    audit_module = "academics"


class CampusAcademicsViewSet(InUseProtectionMixin, CampusScopedViewSet):
    audit_module = "academics"


# ---------------------------------------------------------------------------
# Organization-wide structure
# ---------------------------------------------------------------------------
@_schema("academics", "department")
class DepartmentViewSet(AcademicsViewSet):
    queryset = Department.objects.select_related("head")
    serializer_class = DepartmentSerializer
    search_fields = ["code", "name"]
    ordering_fields = ["name", "code"]
    required_permissions = _perms(STRUCTURE)
    in_use = {"programs": "programs", "subjects": "subjects"}


@_schema("academics", "program")
class ProgramViewSet(AcademicsViewSet):
    queryset = Program.objects.select_related("department")
    serializer_class = ProgramSerializer
    filterset_fields = ["department", "level_type", "is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["name", "code"]
    required_permissions = _perms(STRUCTURE)
    in_use = {"sections": "sections", "batches": "batches", "curriculum": "curriculum entries"}


@_schema("academics", "subject")
class SubjectViewSet(AcademicsViewSet):
    queryset = Subject.objects.select_related("department")
    serializer_class = SubjectSerializer
    filterset_fields = ["department"]
    search_fields = ["code", "name"]
    ordering_fields = ["name", "code"]
    required_permissions = _perms(STRUCTURE)
    in_use = {"curriculum_entries": "curriculum entries", "teaching_assignments": "teaching assignments"}


@_schema("academics", "curriculum entry")
class CurriculumSubjectViewSet(AcademicsViewSet):
    """Which subjects each level of a program teaches. Filter with
    ``?program=<id>&level=<n>`` to get one grade's or semester's syllabus."""

    queryset = CurriculumSubject.objects.select_related("program", "subject")
    serializer_class = CurriculumSubjectSerializer
    filterset_fields = ["program", "level", "subject", "is_elective"]
    ordering_fields = ["level"]
    required_permissions = _perms(STRUCTURE)

    def perform_destroy(self, instance):
        if TeachingAssignment.objects.filter(
            section__program_id=instance.program_id,
            section__level=instance.level,
            subject_id=instance.subject_id,
        ).exists():
            raise ConflictError(
                "Sections are being taught this subject. Remove those teaching assignments first.",
                code="in_use",
            )
        super().perform_destroy(instance)


@_schema("academics", "academic year")
class AcademicYearViewSet(AcademicsViewSet):
    queryset = AcademicYear.objects.all()
    serializer_class = AcademicYearSerializer
    filterset_fields = ["is_current"]
    search_fields = ["name"]
    ordering_fields = ["start_date", "name"]
    required_permissions = _perms(STRUCTURE, set_current=[STRUCTURE])
    in_use = {"sections": "sections", "intake_batches": "batches"}

    @extend_schema(tags=["academics"], summary="Make this the current academic year", request=None,
                   responses={200: AcademicYearSerializer})
    @action(detail=True, methods=["post"], url_path="set-current")
    def set_current(self, request, pk=None):
        year = self.get_object()
        with transaction.atomic():
            previous = (
                AcademicYear.objects.select_for_update()
                .filter(organization_id=year.organization_id, is_current=True)
                .exclude(pk=year.pk)
                .first()
            )
            if previous is not None:
                previous.is_current = False
                previous.save(update_fields=["is_current", "updated_at"])
            year.is_current = True
            year.save(update_fields=["is_current", "updated_at"])
            log(AuditLog.Action.UPDATE, instance=year, module="academics", actor=request.user,
                changes={"is_current": {"before": False, "after": True}},
                metadata={"previous_current": previous.pk if previous else None})
        return Response(AcademicYearSerializer(year, context=self.get_serializer_context()).data)


@_schema("academics", "term")
class TermViewSet(AcademicsViewSet):
    queryset = Term.objects.select_related("academic_year")
    serializer_class = TermSerializer
    filterset_fields = ["academic_year"]
    ordering_fields = ["sequence", "start_date"]
    required_permissions = _perms(STRUCTURE)
    in_use = {"timetable_entries": "timetable entries"}


# ---------------------------------------------------------------------------
# Campus-level: rooms, batches, sections, who teaches what
# ---------------------------------------------------------------------------
@_schema("academics", "room")
class RoomViewSet(CampusAcademicsViewSet):
    queryset = Room.objects.select_related("campus")
    serializer_class = RoomSerializer
    filterset_fields = ["campus", "room_type", "is_active"]
    search_fields = ["code", "name", "building"]
    ordering_fields = ["code", "name", "capacity"]
    required_permissions = _perms(CLASSES)
    in_use = {"timetable_entries": "timetable entries"}


@_schema("academics", "batch")
class BatchViewSet(CampusAcademicsViewSet):
    queryset = Batch.objects.select_related("program", "campus", "start_year")
    serializer_class = BatchSerializer
    filterset_fields = ["program", "campus", "start_year", "is_active"]
    search_fields = ["code", "name"]
    ordering_fields = ["name", "code"]
    required_permissions = _perms(CLASSES)
    in_use = {"sections": "sections"}


@_schema("academics", "section")
class SectionViewSet(CampusAcademicsViewSet):
    """Class groups. ``?academic_year=&program=&level=`` lists one grade."""

    queryset = Section.objects.select_related(
        "academic_year", "campus", "program", "class_teacher", "home_room", "batch"
    ).annotate(
        student_count=Count("enrollments", filter=Q(enrollments__status=Enrollment.Status.ACTIVE))
    )
    serializer_class = SectionSerializer
    filterset_fields = ["academic_year", "campus", "program", "level", "batch", "class_teacher"]
    search_fields = ["name", "program__name"]
    ordering_fields = ["level", "name"]
    ordering = ["program__name", "level", "name", "pk"]
    required_permissions = _perms(CLASSES, students=[VIEW, "students.view"],
                                  promote=["students.place"])
    in_use = {"enrollments": "student placements (current or past)",
              "teaching_assignments": "teaching assignments"}

    @extend_schema(tags=["academics"], summary="Students currently in a section",
                   parameters=[OpenApiParameter("subject", int, description=(
                       "Only students who take this subject: everyone for a compulsory "
                       "subject, those who chose it for an elective."))],
                   responses={200: SectionStudentSerializer(many=True)})
    @action(detail=True, methods=["get"])
    def students(self, request, pk=None):
        section = self.get_object()
        subject = request.query_params.get("subject")
        if subject is not None:
            if not subject.isdigit():
                raise ValidationError({"subject": "Must be a subject id."})
            students = students_taking(section, int(subject))
        else:
            students = students_in_section(section)
        students = students.order_by("first_name", "last_name")
        return Response(SectionStudentSerializer(students, many=True).data)

    @extend_schema(tags=["academics"], summary="Move a whole class to another section",
                   description="End-of-year promotion, or merging sections. All or nothing: "
                               "if any student can't move, nobody does (409 promotion_failed).",
                   request=PromoteSectionSerializer, responses={200: SectionStudentSerializer(many=True)})
    @action(detail=True, methods=["post"])
    def promote(self, request, pk=None):
        section = self.get_object()
        serializer = PromoteSectionSerializer(data=request.data, context={"section": section})
        serializer.is_valid(raise_exception=True)
        moved = promote_section(section=section, by=request.user, **serializer.validated_data)
        return Response(SectionStudentSerializer(moved, many=True).data)


@_schema("academics", "teaching assignment")
class TeachingAssignmentViewSet(CampusAcademicsViewSet):
    queryset = TeachingAssignment.objects.select_related(
        "section", "section__program", "subject", "teacher"
    )
    serializer_class = TeachingAssignmentSerializer
    campus_field = "section__campus"
    filterset_fields = ["section", "subject", "teacher", "section__academic_year"]
    ordering_fields = ["section", "subject"]
    required_permissions = _perms(CLASSES)
    in_use = {"timetable_entries": "timetable entries"}

    def campus_of(self, validated_data):
        section = validated_data.get("section")
        return section.campus if section is not None else None

    def perform_update(self, serializer):
        if "section" in serializer.validated_data and \
                serializer.validated_data["section"].pk != serializer.instance.section_id:
            raise ValidationError({"section": "Create a new assignment for another section."})
        if "teacher" in serializer.validated_data and \
                serializer.validated_data["teacher"].pk != serializer.instance.teacher_id and \
                serializer.instance.timetable_entries.exists():
            # The new teacher's week was never checked for clashes. Create an
            # assignment for them and move the lessons, which checks each one.
            raise ValidationError({"teacher": "Cannot change: this assignment is on the timetable. "
                                              "Create a new assignment and move its lessons."})
        super().perform_update(serializer)


@extend_schema_view(
    list=extend_schema(tags=["academics"], summary="List students' elective choices"),
    retrieve=extend_schema(tags=["academics"], summary="Retrieve an elective choice"),
    create=extend_schema(tags=["academics"], summary="Record that a student takes an elective"),
    destroy=extend_schema(tags=["academics"], summary="Remove an elective choice"),
)
class StudentElectiveViewSet(CampusAcademicsViewSet):
    """Which elective each student takes in their current class. Filter with
    ``?section=`` or ``?student=``; ``?current=true`` hides earlier classes."""

    http_method_names = ["get", "post", "delete", "head", "options"]
    queryset = StudentElective.objects.select_related(
        "enrollment__student", "enrollment__section__program", "subject"
    )
    serializer_class = StudentElectiveSerializer
    campus_field = "enrollment__campus"
    filterset_fields = {"enrollment__section": ["exact"], "enrollment__student": ["exact"],
                        "subject": ["exact"], "enrollment__status": ["exact"]}
    ordering_fields = ["created_at"]
    required_permissions = {
        "list": [VIEW, "students.view"], "retrieve": [VIEW, "students.view"],
        "create": ["students.place"], "destroy": ["students.place"],
    }

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        # Friendlier names for the two filters everyone uses.
        if params.get("section", "").isdigit():
            qs = qs.filter(enrollment__section_id=params["section"])
        if params.get("student", "").isdigit():
            qs = qs.filter(enrollment__student_id=params["student"])
        if params.get("current") == "true":
            qs = qs.filter(enrollment__status=Enrollment.Status.ACTIVE)
        return qs

    def campus_of(self, validated_data):
        return validated_data["enrollment"].campus_id

    def perform_destroy(self, instance):
        if instance.enrollment.status != Enrollment.Status.ACTIVE:
            raise ConflictError("Choices of an earlier class are history and can't be removed.",
                                code="history")
        super().perform_destroy(instance)
