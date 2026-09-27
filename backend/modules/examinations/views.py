from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.exceptions import ConflictError, PermissionDeniedError
from core.common.mixins import CampusScopedMixin, CampusScopedViewSet, OrganizationScopedMixin, OrganizationScopedViewSet
from core.common.permissions import HasPermission, IsSameOrganization
from modules.academics.models import TeachingAssignment
from modules.attendance.services import org_today
from modules.parents.selectors import links_for_parent, parent_for_user
from modules.staff.selectors import staff_member_for_user
from modules.students.models import Enrollment
from modules.students.models import Student
from modules.students.selectors import student_for_user, students_visible_to

from . import grading, results as results_service, selectors, services
from .models import (
    AdmitCard,
    Exam,
    ExamRoom,
    ExamSubject,
    ExamType,
    GradeScale,
    Invigilation,
    Mark,
    MarkSheet,
    Result,
    ResultPlan,
    SeatAllocation,
)
from .serializers import (
    AddCurriculumSerializer,
    AdmitCardSerializer,
    CorrectMarkSerializer,
    EnterMarksSerializer,
    ExamRoomSerializer,
    ExamSerializer,
    ExamSubjectSerializer,
    ExamTypeSerializer,
    GenerateAdmitCardsSerializer,
    GradeScaleSerializer,
    InvigilationSerializer,
    MarkSerializer,
    MarkSheetSerializer,
    OpenSheetSerializer,
    ReasonSerializer,
    RemarkSerializer,
    ResultListSerializer,
    ResultPlanSerializer,
    ResultSerializer,
    SeatAllocationSerializer,
    SeatPlanSerializer,
    WithholdSerializer,
)
from .services import MANAGE, MARK, PUBLISH, VIEW

TAG = "exams"
GRADES_TAG = "grades"


def _schema(noun: str, *actions, tag=TAG):
    summaries = {"list": f"List {noun}s", "retrieve": f"Retrieve a {noun}",
                 "create": f"Create a {noun}", "update": f"Replace a {noun}",
                 "partial_update": f"Update a {noun}", "destroy": f"Delete a {noun}"}
    return extend_schema_view(**{a: extend_schema(tags=[tag], summary=summaries[a])
                                 for a in actions or summaries})


def _own_student(request):
    """The student behind ``request``: the signed-in student, or a parent's
    child (``?student=`` when they have several)."""
    student = student_for_user(request.user)
    if student is not None:
        return student
    parent = parent_for_user(request.user)
    if parent is None:
        raise NotFound("No student or parent profile is linked to your account.")
    links = list(links_for_parent(parent))
    wanted = request.query_params.get("student")
    if wanted is not None:
        if not wanted.isdigit():
            raise ValidationError({"student": "Must be an id."})
        links = [link for link in links if link.student_id == int(wanted)]
        if not links:
            raise NotFound("That student isn't linked to you.")
    elif len(links) > 1:
        raise ValidationError({"student": "You have several children linked; choose one."})
    if not links:
        raise NotFound("No student is linked to you.")
    return links[0].student


class ExamsViewSet(CampusScopedViewSet):
    audit_module = "examinations"


# ---------------------------------------------------------------------------
# Grade scales and exam types
# ---------------------------------------------------------------------------
@_schema("grade scale", tag=GRADES_TAG)
class GradeScaleViewSet(OrganizationScopedViewSet):
    """How percentages become letters, grade points and pass/fail. A scale
    with no program is the organization's default."""

    queryset = GradeScale.objects.select_related("program").prefetch_related("bands", "divisions")
    serializer_class = GradeScaleSerializer
    audit_module = "examinations"
    filterset_fields = ["program"]
    required_permissions = {
        "list": ["grades.view"], "retrieve": ["grades.view"], "grade": ["grades.view"],
        "create": ["grades.manage"], "update": ["grades.manage"], "partial_update": ["grades.manage"],
        "destroy": ["grades.manage"],
    }

    def perform_destroy(self, instance):
        if instance.exams.exists() or instance.result_plans.exists():
            raise ConflictError("Exams or term results use this scale.", code="in_use")
        super().perform_destroy(instance)

    @extend_schema(tags=[GRADES_TAG], summary="The grade a percentage earns on this scale",
                   parameters=[OpenApiParameter("percentage", float, required=True)], responses={200: None})
    @action(detail=True, methods=["get"])
    def grade(self, request, pk=None):
        try:
            pct = grading.q2(request.query_params["percentage"])
        except Exception:
            raise ValidationError({"percentage": "Give a number from 0 to 100."})
        if not 0 <= pct <= 100:
            raise ValidationError({"percentage": "Give a number from 0 to 100."})
        scale = grading.load_scale(self.get_object())
        band = scale.band_for(pct)
        return Response({"percentage": float(pct), "letter": band.letter, "grade_point": float(band.grade_point),
                         "remark": band.remark, "pass": band.is_pass, "division": scale.division_for(pct)})


@_schema("exam type")
class ExamTypeViewSet(OrganizationScopedViewSet):
    queryset = ExamType.objects.all()
    serializer_class = ExamTypeSerializer
    audit_module = "examinations"
    filterset_fields = ["is_active"]
    search_fields = ["name", "code"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "update": [MANAGE],
                            "partial_update": [MANAGE], "destroy": [MANAGE]}

    def perform_destroy(self, instance):
        if instance.exams.exists():
            raise ConflictError("Exams use this type. Deactivate it instead.", code="in_use")
        super().perform_destroy(instance)


# ---------------------------------------------------------------------------
# Exams
# ---------------------------------------------------------------------------
@_schema("exam")
class ExamViewSet(ExamsViewSet):
    """An exam for one program at one campus and year. Build it (papers,
    rooms), schedule it, run it (seats, admit cards, marks), then publish."""

    queryset = Exam.objects.select_related("exam_type", "program", "campus", "academic_year", "term",
                                           "grade_scale", "organization")
    serializer_class = ExamSerializer
    filterset_fields = ["campus", "academic_year", "term", "exam_type", "program", "status", "on_transcript"]
    search_fields = ["name"]
    ordering_fields = ["start_date", "name", "created_at"]
    required_permissions = {
        "list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "update": [MANAGE], "partial_update": [MANAGE],
        "destroy": [MANAGE], "add_curriculum": [MANAGE], "schedule": [MANAGE], "unschedule": [MANAGE],
        "publish": [PUBLISH], "unpublish": [PUBLISH], "readiness": [VIEW], "summary": [VIEW],
        "compute": [MANAGE], "seat_plan": [MANAGE], "clear_seat_plan": [MANAGE],
        "generate_admit_cards": [MANAGE],
    }

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    def perform_destroy(self, instance):
        if instance.status != Exam.Status.DRAFT:
            raise ConflictError("Only an exam being set up can be deleted.", code="not_draft")
        super().perform_destroy(instance)

    @extend_schema(tags=[TAG], summary="Add a paper for every curriculum subject at some levels",
                   request=AddCurriculumSerializer, responses={201: ExamSubjectSerializer(many=True)})
    @action(detail=True, methods=["post"], url_path="add-curriculum")
    def add_curriculum(self, request, pk=None):
        exam = self.get_object()
        serializer = AddCurriculumSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        papers = services.add_curriculum(exam, **serializer.validated_data)
        return Response(ExamSubjectSerializer(papers, many=True).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Schedule the exam",
                   description="Every paper needs a date, time and marks. Clashes and holidays are refused. "
                               "Creates an exam event on the academic calendar.",
                   request=None, responses={200: ExamSerializer})
    @action(detail=True, methods=["post"])
    def schedule(self, request, pk=None):
        return Response(ExamSerializer(services.schedule(self.get_object(), by=request.user)).data)

    @extend_schema(tags=[TAG], summary="Take the exam back to being set up (only before marks exist)",
                   request=None, responses={200: ExamSerializer})
    @action(detail=True, methods=["post"])
    def unschedule(self, request, pk=None):
        return Response(ExamSerializer(services.unschedule(self.get_object(), by=request.user)).data)

    @extend_schema(tags=[TAG], summary="What still blocks publishing", responses={200: None})
    @action(detail=True, methods=["get"])
    def readiness(self, request, pk=None):
        return Response(results_service.readiness(self.get_object()))

    @extend_schema(tags=[TAG], summary="Work out the results from the marks so far (not published)",
                   request=None, responses={200: None})
    @action(detail=True, methods=["post"])
    def compute(self, request, pk=None):
        exam = self.get_object()
        if exam.status == Exam.Status.DRAFT:
            raise ConflictError("Schedule the exam first.", code="not_scheduled")
        return Response({"results": results_service.compute_exam_results(exam)})

    @extend_schema(tags=[TAG], summary="Publish the results",
                   description="Every mark sheet must be verified and complete. Students and parents "
                               "see results only after this.", request=None, responses={200: None})
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        return Response({"results": results_service.publish_exam(self.get_object(), by=request.user)})

    @extend_schema(tags=[TAG], summary="Withdraw published results", request=ReasonSerializer,
                   responses={200: ExamSerializer})
    @action(detail=True, methods=["post"])
    def unpublish(self, request, pk=None):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exam = results_service.unpublish_exam(self.get_object(), serializer.validated_data["reason"],
                                              by=request.user)
        return Response(ExamSerializer(exam).data)

    @extend_schema(tags=[TAG], summary="How the class did: pass rates, averages, toppers", responses={200: None})
    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        return Response(selectors.exam_summary(self.get_object()))

    @extend_schema(tags=[TAG], summary="Give every candidate a room and seat", request=SeatPlanSerializer,
                   responses={200: None})
    @action(detail=True, methods=["post"], url_path="seat-plan")
    def seat_plan(self, request, pk=None):
        serializer = SeatPlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.generate_seat_plan(self.get_object(), by=request.user, **serializer.validated_data))

    @extend_schema(tags=[TAG], summary="Remove the seat plan", request=None, responses={204: None})
    @action(detail=True, methods=["post"], url_path="clear-seat-plan")
    def clear_seat_plan(self, request, pk=None):
        exam = self.get_object()
        services.ensure_open_for_structure(exam)
        exam.seats.all().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=[TAG], summary="Issue admit cards to every candidate",
                   description="People below the attendance minimum get a withheld card. Existing cards "
                               "are left alone, so it is safe to run again.",
                   request=GenerateAdmitCardsSerializer, responses={200: None})
    @action(detail=True, methods=["post"], url_path="generate-admit-cards")
    def generate_admit_cards(self, request, pk=None):
        exam = self.get_object()
        serializer = GenerateAdmitCardsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        section = serializer.validated_data.get("section")
        if section is not None:
            self.check_campus_allowed(section.campus_id)
            if section.organization_id != request.user.organization_id:
                raise ValidationError({"section": "Unknown section."})
        return Response(services.generate_admit_cards(exam, by=request.user, section=section))

    @extend_schema(tags=[TAG], summary="My exams (students), or a child's (parents)",
                   description="Scheduled and published exams for the student's class, with the papers, "
                               "seat and admit card.",
                   parameters=[OpenApiParameter("student", int, description="Parents: which child.")],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def me(self, request):
        student = _own_student(request)
        enrollment = (Enrollment.objects.on(org_today(request.user.organization))
                      .filter(student=student).select_related("section__program").first())
        if enrollment is None or enrollment.section is None:
            return Response([])
        section = enrollment.section
        exams = Exam.objects.filter(
            organization_id=request.user.organization_id, campus_id=section.campus_id,
            program_id=section.program_id, academic_year_id=section.academic_year_id,
            status__in=[Exam.Status.SCHEDULED, Exam.Status.PUBLISHED], subjects__level=section.level,
        ).select_related("exam_type").distinct().order_by("start_date")
        out = []
        for exam in exams:
            card = AdmitCard.objects.filter(exam=exam, enrollment=enrollment).first()
            seat = SeatAllocation.objects.filter(exam=exam, enrollment=enrollment).select_related(
                "exam_room__room").first()
            papers = exam.subjects.filter(level=section.level).select_related("subject").order_by("date", "start_time")
            out.append({
                "id": exam.pk, "name": exam.name, "type": exam.exam_type.name, "status": exam.status,
                "start_date": exam.start_date, "end_date": exam.end_date, "instructions": exam.instructions,
                "papers": [{"subject_name": p.subject.name, "date": p.date, "start_time": p.start_time,
                            "end_time": p.end_time} for p in papers],
                "seat": None if seat is None else {"room": seat.exam_room.room.name,
                                                   "seat_number": seat.seat_number},
                "admit_card": None if card is None else {"id": card.pk, "card_number": card.card_number,
                                                         "status": card.status,
                                                         "withheld_reason": card.withheld_reason},
            })
        return Response(out)


class ExamChildViewSet(ExamsViewSet):
    """Base for records that hang off an exam and take its campus."""

    def campus_of(self, validated_data):
        exam = validated_data.get("exam")
        return exam.campus if exam is not None else None


@_schema("exam paper")
class ExamSubjectViewSet(ExamChildViewSet):
    """A paper: a subject at one level, with its date, time and marks components."""

    queryset = ExamSubject.objects.select_related("exam", "subject").prefetch_related("components")
    serializer_class = ExamSubjectSerializer
    campus_field = "exam__campus"
    filterset_fields = ["exam", "level", "subject", "date"]
    ordering_fields = ["date", "level"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "update": [MANAGE],
                            "partial_update": [MANAGE], "destroy": [MANAGE]}

    def perform_destroy(self, instance):
        services.ensure_open_for_structure(instance.exam)
        services.ensure_no_marks(instance)
        exam = instance.exam
        super().perform_destroy(instance)
        services.reschedule_paper(ExamSubject(exam=exam))


@_schema("exam room")
class ExamRoomViewSet(ExamChildViewSet):
    queryset = ExamRoom.objects.select_related("exam", "room")
    serializer_class = ExamRoomSerializer
    campus_field = "exam__campus"
    filterset_fields = ["exam"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "update": [MANAGE],
                            "partial_update": [MANAGE], "destroy": [MANAGE]}

    def perform_destroy(self, instance):
        services.ensure_open_for_structure(instance.exam)
        super().perform_destroy(instance)


@_schema("seat", "list", "retrieve")
class SeatAllocationViewSet(ExamsViewSet):
    """Who sits where. Made with `POST /exams/{id}/seat-plan/`."""

    http_method_names = ["get", "head", "options"]
    queryset = SeatAllocation.objects.select_related("enrollment__student", "enrollment__section__program",
                                                     "exam_room__room")
    serializer_class = SeatAllocationSerializer
    campus_field = "exam__campus"
    filterset_fields = ["exam", "exam_room", "enrollment__section"]
    ordering_fields = ["seat_number"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW]}


@_schema("invigilator duty")
class InvigilationViewSet(ExamsViewSet):
    queryset = Invigilation.objects.select_related("exam_subject__subject", "exam_subject__exam",
                                                   "exam_room__room", "staff")
    serializer_class = InvigilationSerializer
    campus_field = "exam_subject__exam__campus"
    filterset_fields = ["exam_subject", "exam_subject__exam", "exam_room", "staff"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "update": [MANAGE],
                            "partial_update": [MANAGE], "destroy": [MANAGE]}

    def campus_of(self, validated_data):
        paper = validated_data.get("exam_subject")
        return paper.exam.campus if paper is not None else None


@_schema("admit card", "list", "retrieve")
class AdmitCardViewSet(ExamsViewSet):
    http_method_names = ["get", "post", "head", "options"]
    queryset = AdmitCard.objects.select_related("student", "enrollment__section__program", "exam")
    serializer_class = AdmitCardSerializer
    campus_field = "exam__campus"
    filterset_fields = ["exam", "status", "enrollment__section"]
    search_fields = ["card_number", "student__first_name", "student__last_name", "student__student_number"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "data": [VIEW], "withhold": [MANAGE],
                            "release": [MANAGE]}

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    @extend_schema(tags=[TAG], summary="Everything printed on the admit card", responses={200: None})
    @action(detail=True, methods=["get"])
    def data(self, request, pk=None):
        return Response(selectors.admit_card_data(self.get_object()))

    @extend_schema(tags=[TAG], summary="Withhold an admit card", request=WithholdSerializer,
                   responses={200: AdmitCardSerializer})
    @action(detail=True, methods=["post"])
    def withhold(self, request, pk=None):
        serializer = WithholdSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        card = services.withhold_card(self.get_object(), serializer.validated_data["reason"], by=request.user)
        return Response(AdmitCardSerializer(card).data)

    @extend_schema(tags=[TAG], summary="Release a withheld admit card", request=None,
                   responses={200: AdmitCardSerializer})
    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        return Response(AdmitCardSerializer(services.release_card(self.get_object(), by=request.user)).data)

    @extend_schema(tags=[TAG], summary="My admit cards (students), or a child's (parents)",
                   parameters=[OpenApiParameter("exam", int), OpenApiParameter("student", int)],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def me(self, request):
        student = _own_student(request)
        cards = AdmitCard.objects.filter(organization_id=request.user.organization_id, student=student,
                                         exam__status__in=[Exam.Status.SCHEDULED, Exam.Status.PUBLISHED])
        if request.query_params.get("exam", "").isdigit():
            cards = cards.filter(exam_id=int(request.query_params["exam"]))
        cards = cards.select_related("exam__exam_type", "exam__campus", "exam__program", "student",
                                     "enrollment__section__program")
        return Response([selectors.admit_card_data(c) for c in cards])


# ---------------------------------------------------------------------------
# Mark sheets and marks
# ---------------------------------------------------------------------------
class MarkSheetFilter(filters.FilterSet):
    exam = filters.NumberFilter(field_name="exam_subject__exam")

    class Meta:
        model = MarkSheet
        fields = ["exam", "exam_subject", "section", "status"]


@_schema("mark sheet", "list", "retrieve")
class MarkSheetViewSet(ExamsViewSet):
    """The marks of one paper for one class. A teacher opens the sheet, enters
    marks, and submits; the exam office verifies it (or sends it back)."""

    http_method_names = ["get", "post", "head", "options"]
    queryset = MarkSheet.objects.select_related("exam_subject__exam", "exam_subject__subject", "section__program")
    serializer_class = MarkSheetSerializer
    campus_field = "section__campus"
    filterset_class = MarkSheetFilter
    required_permissions = {
        "list": [VIEW], "retrieve": [VIEW], "create": [MARK], "mine": [MARK], "roster": [MARK],
        "marks": [MARK], "submit": [MARK], "verify": [MANAGE], "send_back": [MANAGE],
    }

    def _sheet(self):
        sheet = self.get_object()
        services.ensure_can_enter(self.request.user, sheet)
        return sheet

    @extend_schema(tags=[TAG], summary="Open the mark sheet for a paper and class",
                   description="Idempotent: an existing sheet comes back (200). Refused until the paper "
                               "has been sat.", request=OpenSheetSerializer,
                   responses={200: MarkSheetSerializer, 201: MarkSheetSerializer})
    def create(self, request, *args, **kwargs):
        serializer = OpenSheetSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        self.check_campus_allowed(data["section"].campus_id)
        sheet, created = services.open_sheet(data["exam_subject"], data["section"], by=request.user)
        return Response(MarkSheetSerializer(sheet).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @extend_schema(tags=[TAG], summary="My papers to enter marks for", responses={200: None})
    @action(detail=False, methods=["get"])
    def mine(self, request):
        staff = staff_member_for_user(request.user)
        if staff is None:
            raise NotFound("No staff profile is linked to your account.")
        today = org_today(request.user.organization)
        sheets = {(s.exam_subject_id, s.section_id): s for s in MarkSheet.objects.filter(
            organization_id=request.user.organization_id)}
        items = []
        for ta in (TeachingAssignment.objects.filter(teacher=staff, is_active=True)
                   .select_related("section__program", "subject")):
            section = ta.section
            papers = ExamSubject.objects.filter(
                subject_id=ta.subject_id, level=section.level, exam__campus_id=section.campus_id,
                exam__program_id=section.program_id, exam__academic_year_id=section.academic_year_id,
                exam__status__in=[Exam.Status.SCHEDULED, Exam.Status.PUBLISHED], exam__deleted_at__isnull=True,
            ).select_related("exam", "subject").order_by("date")
            for paper in papers:
                sheet = sheets.get((paper.pk, section.pk))
                items.append({
                    "exam": paper.exam_id, "exam_name": paper.exam.name, "exam_subject": paper.pk,
                    "subject_name": paper.subject.name, "date": paper.date, "held": paper.date <= today,
                    "section": section.pk, "section_name": section.display_name,
                    "sheet": sheet.pk if sheet else None, "status": sheet.status if sheet else None,
                })
        return Response(items)

    @extend_schema(tags=[TAG], summary="Who sits the paper, the components, and the marks so far",
                   responses={200: None})
    @action(detail=True, methods=["get"])
    def roster(self, request, pk=None):
        sheet = self.get_object()
        if not services.holds(request.user, VIEW, sheet.section.campus_id):
            services.ensure_can_enter(request.user, sheet)
        paper = sheet.exam_subject
        components = list(paper.components.all())
        marks = {}
        for mark in sheet.marks.all():
            marks.setdefault(mark.enrollment_id, {})[mark.component_id] = {
                "status": mark.status, "marks": None if mark.marks is None else float(mark.marks)}
        cards = dict(AdmitCard.objects.filter(exam=paper.exam).values_list("enrollment_id", "status"))
        students = [{
            "enrollment": e.pk, "student": e.student_id, "student_name": e.student.full_name,
            "student_number": e.student.student_number, "admit_card": cards.get(e.pk),
            "marks": {str(cid): marks.get(e.pk, {}).get(cid) for cid in (c.pk for c in components)},
        } for e in services.expected_enrollments(paper, sheet.section)]
        return Response({
            "sheet": MarkSheetSerializer(sheet).data,
            "components": [{"id": c.pk, "name": c.name, "kind": c.kind, "full_marks": float(c.full_marks),
                            "pass_marks": float(c.pass_marks)} for c in components],
            "students": students,
        })

    @extend_schema(tags=[TAG], summary="Enter or change marks",
                   description="Several students and components at once. While the sheet is open that's "
                               "free. After it's submitted only the exam office may change a mark, and "
                               "must give a `reason`; the old value is kept as a correction.",
                   request=EnterMarksSerializer, responses={200: MarkSerializer(many=True)})
    @action(detail=True, methods=["post"])
    def marks(self, request, pk=None):
        sheet = self.get_object()
        serializer = EnterMarksSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entries = [services.MarkEntry(enrollment_id=e["enrollment"], component_id=e["component"],
                                      status=e["status"], marks=e.get("marks"))
                   for e in serializer.validated_data["entries"]]
        saved = services.enter_marks(sheet=sheet, entries=entries, by=request.user,
                                     reason=serializer.validated_data["reason"])
        return Response(MarkSerializer(saved, many=True).data)

    @extend_schema(tags=[TAG], summary="Submit the marks to the exam office",
                   description="409 `unmarked` lists who still has no mark.", request=None,
                   responses={200: MarkSheetSerializer})
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        return Response(MarkSheetSerializer(services.submit_sheet(self.get_object(), by=request.user)).data)

    @extend_schema(tags=[TAG], summary="Verify submitted marks (exam office)", request=None,
                   responses={200: MarkSheetSerializer})
    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        return Response(MarkSheetSerializer(services.verify_sheet(self.get_object(), by=request.user)).data)

    @extend_schema(tags=[TAG], summary="Send the marks back to the teacher (exam office)",
                   request=ReasonSerializer, responses={200: MarkSheetSerializer})
    @action(detail=True, methods=["post"], url_path="send-back")
    def send_back(self, request, pk=None):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sheet = services.send_back(self.get_object(), serializer.validated_data["reason"], by=request.user)
        return Response(MarkSheetSerializer(sheet).data)


@_schema("mark", "list", "retrieve", "partial_update")
class MarkViewSet(ExamsViewSet):
    """Individual marks, with their correction history. PATCH corrects one."""

    http_method_names = ["get", "patch", "head", "options"]
    queryset = Mark.objects.select_related(
        "component", "enrollment__student", "sheet__section", "sheet__exam_subject__exam",
    ).prefetch_related("corrections")
    serializer_class = MarkSerializer
    campus_field = "sheet__section__campus"
    filterset_fields = ["sheet", "component", "enrollment", "status"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "partial_update": [MARK]}

    def get_serializer_class(self):
        return CorrectMarkSerializer if self.action == "partial_update" else MarkSerializer

    @extend_schema(tags=[TAG], summary="Correct one mark", request=CorrectMarkSerializer,
                   responses={200: MarkSerializer})
    def partial_update(self, request, *args, **kwargs):
        mark = self.get_object()
        serializer = CorrectMarkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        services.enter_marks(
            sheet=mark.sheet, by=request.user, reason=data["reason"],
            entries=[services.MarkEntry(mark.enrollment_id, mark.component_id, data["status"], data.get("marks"))])
        return Response(MarkSerializer(self.get_queryset().get(pk=mark.pk)).data)


# ---------------------------------------------------------------------------
# Results, report cards, transcripts
# ---------------------------------------------------------------------------
class ResultFilter(filters.FilterSet):
    level = filters.NumberFilter(field_name="section__level")

    class Meta:
        model = Result
        fields = ["exam", "plan", "section", "student", "status", "level"]


@_schema("result", "list", "retrieve")
class ResultViewSet(CampusScopedMixin, OrganizationScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Computed results. Staff see them as soon as they're computed; students
    and parents (via `me`) only once published."""

    queryset = Result.objects.select_related("student", "section__program", "exam", "plan").prefetch_related(
        "subjects__subject")
    serializer_class = ResultSerializer
    campus_field = "section__campus"
    permission_classes = [HasPermission, IsSameOrganization]
    filterset_class = ResultFilter
    search_fields = ["student__first_name", "student__last_name", "student__student_number"]
    ordering_fields = ["percentage", "rank_in_section", "rank_in_level", "grade_point"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "report_card": [VIEW], "remark": [MARK]}

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_serializer_class(self):
        return ResultListSerializer if self.action == "list" else ResultSerializer

    @extend_schema(tags=[TAG], summary="A result's report card", responses={200: None})
    @action(detail=True, methods=["get"], url_path="report-card")
    def report_card(self, request, pk=None):
        return Response(selectors.report_card(self.get_object()))

    @extend_schema(tags=[TAG], summary="Write the class teacher's remark on a result",
                   request=RemarkSerializer, responses={200: ResultSerializer})
    @action(detail=True, methods=["post"])
    def remark(self, request, pk=None):
        result = self.get_object()
        campus_id = result.section.campus_id
        staff = staff_member_for_user(request.user)
        is_class_teacher = staff is not None and staff.pk == result.section.class_teacher_id
        if not (services.holds(request.user, MANAGE, campus_id) or is_class_teacher):
            raise PermissionDeniedError("Only the class teacher or the exam office can write remarks.",
                                        code="not_your_class")
        serializer = RemarkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result.remark, result.remark_by = serializer.validated_data["remark"], request.user
        result.save(update_fields=["remark", "remark_by", "updated_at"])
        return Response(ResultSerializer(result).data)

    @extend_schema(tags=[TAG], summary="My published results (students), or a child's (parents)",
                   parameters=[OpenApiParameter("student", int, description="Parents: which child."),
                               OpenApiParameter("include_card", bool)], responses={200: None})
    @action(detail=False, methods=["get"])
    def me(self, request):
        student = _own_student(request)
        results = (selectors.published_results().filter(organization_id=request.user.organization_id,
                                                        student=student)
                   .select_related("student", "section__program", "exam", "plan")
                   .prefetch_related("subjects__subject").order_by("-updated_at"))
        if request.query_params.get("include_card") == "true":
            return Response([selectors.report_card(r) for r in results])
        return Response(ResultSerializer(results, many=True).data)


class ReportCardViewSet(viewsets.GenericViewSet):
    """Report cards as data: subjects, marks, grades, GPA, rank and attendance."""

    queryset = Result.objects.all()
    permission_classes = [HasPermission, IsSameOrganization]
    pagination_class = None
    required_permissions = {"list": [VIEW]}

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    @extend_schema(tags=[TAG], summary="Report cards for a class or a student",
                   parameters=[OpenApiParameter("exam", int), OpenApiParameter("plan", int),
                               OpenApiParameter("section", int), OpenApiParameter("student", int)],
                   responses={200: None})
    def list(self, request):
        params = request.query_params
        source = {k: params[k] for k in ("exam", "plan") if params.get(k, "").isdigit()}
        if len(source) != 1:
            raise ValidationError("Give exactly one of exam or plan.")
        if not (params.get("section", "").isdigit() or params.get("student", "").isdigit()):
            raise ValidationError("Give a section or a student.")
        results = Result.objects.filter(organization_id=request.user.organization_id, **{
            f"{k}_id": int(v) for k, v in source.items()})
        campus_ids = services.campus_ids_with_permission(request.user, VIEW)
        if campus_ids is not None:
            results = results.filter(section__campus_id__in=campus_ids)
        if params.get("section", "").isdigit():
            results = results.filter(section_id=int(params["section"]))
        if params.get("student", "").isdigit():
            results = results.filter(student_id=int(params["student"]))
        results = results.select_related("student", "section__program", "section__campus", "exam", "plan",
                                         "enrollment").prefetch_related("subjects__subject")
        return Response([selectors.report_card(r) for r in results.order_by("section", "student__first_name")])

    @extend_schema(tags=[TAG], summary="My report card (students), or a child's (parents)",
                   parameters=[OpenApiParameter("exam", int), OpenApiParameter("plan", int),
                               OpenApiParameter("student", int)], responses={200: None})
    @action(detail=False, methods=["get"])
    def me(self, request):
        student = _own_student(request)
        results = selectors.published_results().filter(organization_id=request.user.organization_id, student=student)
        params = request.query_params
        for key in ("exam", "plan"):
            if params.get(key, "").isdigit():
                results = results.filter(**{f"{key}_id": int(params[key])})
        result = results.select_related("student", "section__program", "section__campus", "exam", "plan",
                                        "enrollment").prefetch_related("subjects__subject").order_by(
            "-updated_at").first()
        if result is None:
            raise NotFound("No published result yet.")
        return Response(selectors.report_card(result))


class TranscriptViewSet(viewsets.GenericViewSet):
    """A student's whole record of published results, with a cumulative GPA."""

    queryset = Student.objects.all()
    permission_classes = [HasPermission, IsSameOrganization]
    pagination_class = None
    required_permissions = {"retrieve": [VIEW]}

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    @extend_schema(tags=[TAG], summary="A student's transcript", responses={200: None})
    def retrieve(self, request, pk=None):
        student = students_visible_to(request.user, VIEW).filter(pk=pk).first()
        if student is None:
            raise NotFound("No such student.")
        return Response(selectors.transcript(student))

    @extend_schema(tags=[TAG], summary="My transcript (students), or a child's (parents)",
                   parameters=[OpenApiParameter("student", int)], responses={200: None})
    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response(selectors.transcript(_own_student(request)))


# ---------------------------------------------------------------------------
# Term results
# ---------------------------------------------------------------------------
@_schema("term result")
class ResultPlanViewSet(ExamsViewSet):
    """A term result made from several exams, each counting for a share."""

    queryset = ResultPlan.objects.select_related("campus", "academic_year", "term", "program",
                                                 "grade_scale").prefetch_related("items__exam")
    serializer_class = ResultPlanSerializer
    filterset_fields = ["campus", "academic_year", "term", "program", "status"]
    required_permissions = {
        "list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "update": [MANAGE], "partial_update": [MANAGE],
        "destroy": [MANAGE], "compute": [MANAGE], "publish": [PUBLISH], "unpublish": [PUBLISH],
    }

    def perform_destroy(self, instance):
        if instance.is_published:
            raise ConflictError("It's published. Unpublish it first.", code="plan_published")
        super().perform_destroy(instance)

    @extend_schema(tags=[TAG], summary="Work out the term results from the exams (not published)",
                   request=None, responses={200: None})
    @action(detail=True, methods=["post"])
    def compute(self, request, pk=None):
        plan = self.get_object()
        if plan.is_published:
            raise ConflictError("It's published. Unpublish it first.", code="plan_published")
        return Response({"results": results_service.compute_plan_results(plan)})

    @extend_schema(tags=[TAG], summary="Publish the term results", request=None, responses={200: None})
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        return Response({"results": results_service.publish_plan(self.get_object(), by=request.user)})

    @extend_schema(tags=[TAG], summary="Withdraw published term results", request=ReasonSerializer,
                   responses={200: ResultPlanSerializer})
    @action(detail=True, methods=["post"])
    def unpublish(self, request, pk=None):
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = results_service.unpublish_plan(self.get_object(), serializer.validated_data["reason"],
                                              by=request.user)
        return Response(ResultPlanSerializer(plan).data)
