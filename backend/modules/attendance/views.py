import uuid
from datetime import timedelta

from django.utils.dateparse import parse_date
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.exceptions import ConflictError
from core.common.mixins import CampusScopedMixin, CampusScopedViewSet, OrganizationScopedMixin
from core.common.permissions import HasPermission, IsSameOrganization
from core.permissions.selectors import campus_ids_with_permission
from modules.academics.models import Program, Section
from modules.parents.selectors import links_for_parent, parent_for_user
from modules.staff.models import StaffMember
from modules.staff.selectors import staff_member_for_user
from modules.students.selectors import student_for_user, students_visible_to
from modules.timetable.services import lessons_on

from . import selectors, services
from .models import (
    AttendanceDevice,
    AttendanceRecord,
    AttendanceSession,
    BiometricIdentity,
    Punch,
    Source,
    StaffAttendanceDay,
    StaffWorkSchedule,
    WorkSchedule,
)
from .serializers import (
    BiometricIdentitySerializer,
    CorrectSerializer,
    DeviceSerializer,
    ManualPunchSerializer,
    MarkSerializer,
    OpenSessionSerializer,
    PunchSerializer,
    RecordSerializer,
    ScanSerializer,
    SessionQRSerializer,
    SessionSerializer,
    StaffDayOverrideSerializer,
    StaffDaySerializer,
    StaffQRSerializer,
    StaffScanSerializer,
    StaffWorkScheduleSerializer,
    SubmitSerializer,
    WorkScheduleSerializer,
)
from .services import DEVICES, MANAGE, MARK, VIEW

TAG = "attendance"
MAX_SPAN_DAYS = 400


def _schema(noun: str, *actions):
    summaries = {"list": f"List {noun}s", "retrieve": f"Retrieve a {noun}",
                 "create": f"Create a {noun}", "update": f"Replace a {noun}",
                 "partial_update": f"Update a {noun}", "destroy": f"Delete a {noun}"}
    return extend_schema_view(**{a: extend_schema(tags=[TAG], summary=summaries[a])
                                 for a in actions or summaries})


def _date(request, name, default=None):
    raw = request.query_params.get(name)
    if raw is None:
        if default is None:
            raise ValidationError({name: "Give a date as YYYY-MM-DD."})
        return default
    value = parse_date(raw)
    if value is None:
        raise ValidationError({name: "Give a date as YYYY-MM-DD."})
    return value


def _today(request):
    return services.org_today(request.user.organization)


def _span(request):
    """``from`` and ``to`` query dates; the last 30 days by default."""
    end = _date(request, "to", _today(request))
    start = _date(request, "from", end - timedelta(days=30))
    if start > end:
        raise ValidationError({"from": "Must not be after to."})
    if (end - start).days > MAX_SPAN_DAYS:
        raise ValidationError({"from": f"At most {MAX_SPAN_DAYS} days at a time."})
    return start, end


def _id(request, name, required=True):
    raw = request.query_params.get(name)
    if raw is None:
        if required:
            raise ValidationError({name: "Required."})
        return None
    if not raw.isdigit():
        raise ValidationError({name: "Must be an id."})
    return int(raw)


def _child_of(request, parent):
    links = list(links_for_parent(parent))
    wanted = _id(request, "student", required=False)
    if wanted is not None:
        links = [link for link in links if link.student_id == wanted]
        if not links:
            raise NotFound("That student isn't linked to you.")
    elif len(links) > 1:
        raise ValidationError({"student": "You have several children linked; choose one."})
    if not links:
        raise NotFound("No student is linked to you.")
    return links[0].student


class AttendanceViewSet(CampusScopedViewSet):
    audit_module = "attendance"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
class SessionFilter(filters.FilterSet):
    date_from = filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="date", lookup_expr="lte")

    class Meta:
        model = AttendanceSession
        fields = ["section", "date", "kind", "status", "teacher", "timetable_entry"]


@_schema("attendance session", "list", "retrieve")
class AttendanceSessionViewSet(AttendanceViewSet):
    """Roll calls. A teacher opens one for a lesson or their class's day,
    marks it (in bulk, or by QR), and submits it."""

    http_method_names = ["get", "post", "head", "options"]
    queryset = AttendanceSession.objects.select_related(
        "section__program", "timetable_entry__teaching_assignment__subject",
        "timetable_entry__period", "teacher")
    serializer_class = SessionSerializer
    filterset_class = SessionFilter
    ordering_fields = ["date"]
    required_permissions = {
        "list": [VIEW], "retrieve": [VIEW],
        "create": [MARK], "mine": [MARK], "roster": [MARK], "mark": [MARK], "submit": [MARK],
        "qr": [MARK], "reopen": [MANAGE],
    }

    def get_permissions(self):
        # Students scan with their own account; the service checks who they are.
        if self.action == "scan":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_serializer_class(self):
        return OpenSessionSerializer if self.action == "create" else SessionSerializer

    def _taken_session(self):
        session = self.get_object()
        services.ensure_can_take(self.request.user, session)
        return session

    @extend_schema(tags=[TAG], summary="Open a lesson's or a class's attendance",
                   description="Idempotent: the existing session comes back (200) if it's already "
                               "open. Refused for a future date, a cancelled lesson, a holiday, or "
                               "the wrong mode for the program.",
                   request=OpenSessionSerializer, responses={200: SessionSerializer, 201: SessionSerializer})
    def create(self, request, *args, **kwargs):
        serializer = OpenSessionSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        entry = data.get("timetable_entry")
        section = entry.teaching_assignment.section if entry else data["section"]
        self.check_campus_allowed(section.campus_id)
        session, created = services.open_session(day=data["date"], section=data.get("section"),
                                                 timetable_entry=entry, by=request.user)
        return Response(SessionSerializer(session).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @extend_schema(tags=[TAG], summary="My classes to take attendance for on a day",
                   parameters=[OpenApiParameter("date", str, description="YYYY-MM-DD, default today")],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def mine(self, request):
        day = _date(request, "date", _today(request))
        staff = staff_member_for_user(request.user)
        if staff is None:
            raise NotFound("No staff profile is linked to your account.")
        org_id = request.user.organization_id
        sessions = AttendanceSession.objects.filter(organization_id=org_id, date=day)
        by_entry = {s.timetable_entry_id: s for s in sessions.filter(kind="lesson")}
        by_section = {s.section_id: s for s in sessions.filter(kind="daily")}
        items = []
        for lesson in lessons_on(day, organization_id=org_id, teacher=staff):
            section = lesson.entry.teaching_assignment.section
            if lesson.is_cancelled or section.program.attendance_mode != Program.AttendanceMode.LESSON:
                continue
            session = by_entry.get(lesson.entry.pk)
            items.append({
                "kind": "lesson", "section": section.pk, "section_name": section.display_name,
                "timetable_entry": lesson.entry.pk,
                "subject_name": lesson.entry.teaching_assignment.subject.name,
                "start_time": lesson.entry.period.start_time.isoformat(),
                "end_time": lesson.entry.period.end_time.isoformat(),
                "session": session.pk if session else None,
                "status": session.status if session else None,
            })
        for section in Section.objects.filter(
            class_teacher=staff, program__attendance_mode=Program.AttendanceMode.DAILY,
            academic_year__start_date__lte=day, academic_year__end_date__gte=day,
        ).select_related("program"):
            if services.school_day_problem(section, day):
                continue
            session = by_section.get(section.pk)
            items.append({
                "kind": "daily", "section": section.pk, "section_name": section.display_name,
                "timetable_entry": None, "subject_name": None, "start_time": None, "end_time": None,
                "session": session.pk if session else None,
                "status": session.status if session else None,
            })
        return Response({"date": day.isoformat(), "classes": items})

    @extend_schema(tags=[TAG], summary="Who is expected, and how each is marked so far",
                   responses={200: None})
    @action(detail=True, methods=["get"])
    def roster(self, request, pk=None):
        session = self.get_object()
        if not services.holds(request.user, VIEW, session.campus_id):
            services.ensure_can_take(request.user, session)
        records = {r.enrollment_id: r for r in session.records.all()}
        students = []
        for enrollment in services.expected_enrollments(session).order_by(
                "student__first_name", "student__last_name", "pk"):
            record = records.get(enrollment.pk)
            students.append({
                "enrollment": enrollment.pk, "student": enrollment.student_id,
                "student_name": enrollment.student.full_name,
                "student_number": enrollment.student.student_number,
                "record": record.pk if record else None,
                "status": record.status if record else None,
                "source": record.source if record else None,
                "note": record.note if record else "",
            })
        return Response({"session": SessionSerializer(session).data, "students": students})

    @extend_schema(tags=[TAG], summary="Mark students",
                   description="Several at once. `rest` marks everyone not yet marked, e.g. "
                               "`{\"records\": [{\"enrollment\": 7, \"status\": \"absent\"}], "
                               "\"rest\": \"present\"}`. A record whose `client_key` was already "
                               "stored (a retried offline sync) is skipped.",
                   request=MarkSerializer, responses={200: RecordSerializer(many=True)})
    @action(detail=True, methods=["post"])
    def mark(self, request, pk=None):
        session = self._taken_session()
        serializer = MarkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        marks = [services.Mark(enrollment_id=r["enrollment"], status=r["status"], note=r["note"],
                               client_key=r["client_key"], recorded_at=r.get("recorded_at"))
                 for r in data["records"]]
        staff = staff_member_for_user(request.user)
        is_teacher = staff is not None and staff.pk in (session.teacher_id, session.section.class_teacher_id)
        source = Source.TEACHER if is_teacher else Source.MANUAL
        records = services.mark(session=session, marks=marks, rest=data.get("rest"),
                                by=request.user, source=source)
        return Response(RecordSerializer(records, many=True).data)

    @extend_schema(tags=[TAG], summary="Submit the attendance",
                   description="409 `unmarked` lists who isn't marked, unless `rest` says what "
                               "they are. Submitting twice is harmless.",
                   request=SubmitSerializer, responses={200: SessionSerializer})
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        session = self._taken_session()
        serializer = SubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = services.submit(session=session, rest=serializer.validated_data.get("rest"),
                                  by=request.user)
        return Response(SessionSerializer(session).data)

    @extend_schema(tags=[TAG], summary="Reopen submitted attendance (office)",
                   request=None, responses={200: SessionSerializer})
    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        session = services.reopen(session=self.get_object(), by=request.user)
        return Response(SessionSerializer(session).data)

    @extend_schema(tags=[TAG], summary="A QR code for students to scan",
                   description="Show it on the teacher's screen and ask for a new one before it "
                               "expires. With latitude, longitude and radius, scans from further "
                               "away are refused.",
                   request=SessionQRSerializer, responses={200: None})
    @action(detail=True, methods=["post"])
    def qr(self, request, pk=None):
        session = self._taken_session()
        serializer = SessionQRSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token, expires_at = services.issue_session_qr(session=session, **serializer.validated_data)
        return Response({"token": token, "expires_at": expires_at, "session": session.pk})

    @extend_schema(tags=[TAG], summary="Scan a class's QR code (students)",
                   description="Marks the signed-in student present (or late). 200 with "
                               "`already_marked` if they were marked before.",
                   request=ScanSerializer, responses={200: None, 201: None})
    @action(detail=False, methods=["post"])
    def scan(self, request):
        serializer = ScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record, created = services.scan(user=request.user, **serializer.validated_data)
        return Response(
            {"already_marked": not created, "status": record.status, "session": record.session_id},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
class RecordFilter(filters.FilterSet):
    date_from = filters.DateFilter(field_name="session__date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="session__date", lookup_expr="lte")
    student = filters.NumberFilter(field_name="enrollment__student")
    section = filters.NumberFilter(field_name="session__section")

    class Meta:
        model = AttendanceRecord
        fields = ["session", "status", "source"]


@_schema("attendance record", "list", "retrieve")
class AttendanceRecordViewSet(AttendanceViewSet):
    """Individual marks. PATCH changes one; after submission that's a
    correction, which needs a reason and is kept in ``corrections``."""

    http_method_names = ["get", "patch", "head", "options"]
    queryset = AttendanceRecord.objects.select_related(
        "session__timetable_entry__teaching_assignment__subject", "enrollment__student",
    ).prefetch_related("corrections")
    serializer_class = RecordSerializer
    campus_field = "session__campus"
    filterset_class = RecordFilter
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "partial_update": [MARK]}

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_serializer_class(self):
        return CorrectSerializer if self.action == "partial_update" else RecordSerializer

    @extend_schema(tags=[TAG], summary="Change or correct one record", request=CorrectSerializer,
                   responses={200: RecordSerializer})
    def partial_update(self, request, *args, **kwargs):
        record = self.get_object()
        services.ensure_can_take(request.user, record.session)
        serializer = CorrectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = services.correct(record=record, by=request.user, **serializer.validated_data)
        record = self.get_queryset().get(pk=record.pk)
        return Response(RecordSerializer(record).data)

    @extend_schema(tags=[TAG], summary="My attendance (students), or a child's (parents)",
                   parameters=[OpenApiParameter("from", str), OpenApiParameter("to", str),
                               OpenApiParameter("student", int, description="Parents: which child.")],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def me(self, request):
        start, end = _span(request)
        student = student_for_user(request.user)
        if student is None:
            parent = parent_for_user(request.user)
            if parent is None:
                raise NotFound("No student or parent profile is linked to your account.")
            student = _child_of(request, parent)
        records = selectors.records_between(start, end).filter(
            organization_id=request.user.organization_id, enrollment__student=student,
        ).select_related("session__timetable_entry__teaching_assignment__subject", "enrollment__student")
        return Response({
            "summary": selectors.student_report(student, start, end),
            "records": RecordSerializer(records.order_by("-session__date", "pk"), many=True).data,
        })


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
class AttendanceReportViewSet(CampusScopedMixin, OrganizationScopedMixin, viewsets.GenericViewSet):
    """Registers and reports. Everything is limited to the campuses the
    caller may view attendance at."""

    queryset = AttendanceRecord.objects.all()
    campus_field = "session__campus"
    permission_classes = [HasPermission, IsSameOrganization]
    required_permissions = {"default": [VIEW]}
    pagination_class = None

    def _campus_ids(self):
        return campus_ids_with_permission(self.request.user, VIEW)

    def _sections(self):
        sections = Section.objects.filter(organization_id=self.request.user.organization_id)
        campus_ids = self._campus_ids()
        return sections if campus_ids is None else sections.filter(campus_id__in=campus_ids)

    def _section(self):
        section = self._sections().select_related("program").filter(pk=_id(self.request, "section")).first()
        if section is None:
            raise NotFound("No such section.")
        return section

    @extend_schema(tags=[TAG], summary="One student's attendance, overall and per subject",
                   parameters=[OpenApiParameter("student", int, required=True),
                               OpenApiParameter("from", str), OpenApiParameter("to", str)],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def student(self, request):
        start, end = _span(request)
        student = students_visible_to(request.user, VIEW).filter(pk=_id(request, "student")).first()
        if student is None:
            raise NotFound("No such student.")
        return Response(selectors.student_report(student, start, end, self.get_queryset()))

    @extend_schema(tags=[TAG], summary="A class's register: students × sessions, with totals",
                   parameters=[OpenApiParameter("section", int, required=True),
                               OpenApiParameter("from", str), OpenApiParameter("to", str)],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def register(self, request):
        start, end = _span(request)
        return Response(selectors.section_register(self._section(), start, end))

    @extend_schema(tags=[TAG], summary="Students below an attendance percentage",
                   parameters=[OpenApiParameter("section", int), OpenApiParameter("program", int),
                               OpenApiParameter("below", float, description="Default 75"),
                               OpenApiParameter("from", str), OpenApiParameter("to", str)],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def defaulters(self, request):
        start, end = _span(request)
        try:
            below = float(request.query_params.get("below", 75))
        except ValueError:
            raise ValidationError({"below": "A percentage."})
        sections = self._sections()
        section_id, program_id = _id(request, "section", False), _id(request, "program", False)
        if section_id is not None:
            sections = sections.filter(pk=section_id)
        if program_id is not None:
            sections = sections.filter(program_id=program_id)
        return Response({"from": start.isoformat(), "to": end.isoformat(), "below": below,
                         "students": selectors.defaulters(sections, start, end, below)})

    @extend_schema(tags=[TAG], summary="Attendance not taken or not submitted on a day",
                   parameters=[OpenApiParameter("date", str, description="Default today"),
                               OpenApiParameter("campus", int)],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def missing(self, request):
        day = _date(request, "date", _today(request))
        sections = self._sections()
        campus_id = _id(request, "campus", False)
        if campus_id is not None:
            sections = sections.filter(campus_id=campus_id)
        return Response({"date": day.isoformat(), "missing": selectors.missing(
            day, organization_id=request.user.organization_id, sections=sections)})

    @extend_schema(tags=[TAG], summary="Staff attendance: days present, late, absent …",
                   parameters=[OpenApiParameter("campus", int), OpenApiParameter("staff", int),
                               OpenApiParameter("from", str), OpenApiParameter("to", str)],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def staff(self, request):
        start, end = _span(request)
        staff = StaffMember.objects.filter(organization_id=request.user.organization_id).select_related("campus")
        campus_ids = self._campus_ids()
        if campus_ids is not None:
            staff = staff.filter(campus_id__in=campus_ids)
        for name in ("campus", "staff"):
            value = _id(request, name, False)
            if value is not None:
                staff = staff.filter(**{"campus_id" if name == "campus" else "pk": value})
        staff = staff.exclude(status=StaffMember.Status.LEFT, left_on__lte=start)
        return Response({"from": start.isoformat(), "to": end.isoformat(),
                         "staff": selectors.staff_report(list(staff), start, end)})


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------
@_schema("work schedule")
class WorkScheduleViewSet(AttendanceViewSet):
    queryset = WorkSchedule.objects.select_related("campus")
    serializer_class = WorkScheduleSerializer
    filterset_fields = ["campus", "is_default"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE],
                            "update": [MANAGE], "partial_update": [MANAGE], "destroy": [MANAGE]}

    def perform_destroy(self, instance):
        if instance.staff.exists():
            raise ConflictError("Staff members still use this schedule.", code="in_use")
        super().perform_destroy(instance)


@_schema("staff work schedule")
class StaffWorkScheduleViewSet(AttendanceViewSet):
    """A staff member's own schedule, instead of their campus's default."""

    queryset = StaffWorkSchedule.objects.select_related("staff", "schedule")
    serializer_class = StaffWorkScheduleSerializer
    campus_field = "staff__campus"
    filterset_fields = ["staff", "schedule"]
    required_permissions = WorkScheduleViewSet.required_permissions

    def campus_of(self, validated_data):
        staff = validated_data.get("staff")
        return staff.campus_id if staff else None


@_schema("staff attendance day", "list", "retrieve", "destroy")
class StaffAttendanceDayViewSet(AttendanceViewSet):
    """Staff days, worked out from punches. POST sets a day by hand (leave,
    on duty, a forgotten punch); DELETE hands it back to the punches."""

    http_method_names = ["get", "post", "delete", "head", "options"]
    queryset = StaffAttendanceDay.objects.select_related("staff")
    serializer_class = StaffDaySerializer
    campus_field = "staff__campus"
    filterset_fields = ["staff", "date", "status", "is_override"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE],
                            "destroy": [MANAGE]}

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_serializer_class(self):
        return StaffDayOverrideSerializer if self.action == "create" else StaffDaySerializer

    @extend_schema(tags=[TAG], summary="Set a staff member's day by hand",
                   request=StaffDayOverrideSerializer, responses={200: StaffDaySerializer})
    def create(self, request, *args, **kwargs):
        serializer = StaffDayOverrideSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        self.check_campus_allowed(data["staff"].campus_id)
        staff_day = services.set_staff_day(staff=data["staff"], day=data["date"], status=data["status"],
                                           note=data["note"], by=request.user)
        return Response(StaffDaySerializer(staff_day).data)

    def destroy(self, request, *args, **kwargs):
        staff_day = self.get_object()
        if not staff_day.is_override:
            raise ConflictError("Only a day set by hand can be cleared; this one comes from punches.",
                                code="not_override")
        services.clear_staff_day_override(staff_day=staff_day, by=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=[TAG], summary="My attendance (staff)",
                   parameters=[OpenApiParameter("from", str), OpenApiParameter("to", str)],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def me(self, request):
        start, end = _span(request)
        staff = staff_member_for_user(request.user)
        if staff is None:
            raise NotFound("No staff profile is linked to your account.")
        days = StaffAttendanceDay.objects.filter(staff=staff, date__gte=start, date__lte=end)
        return Response({"summary": selectors.staff_report([staff], start, end)[0],
                         "days": StaffDaySerializer(days.order_by("-date"), many=True).data})


@_schema("punch", "list", "retrieve")
class PunchViewSet(AttendanceViewSet):
    """Raw check-ins and check-outs, from devices, QR and the office.
    Never edited; a wrong day is fixed by setting the staff day by hand."""

    http_method_names = ["get", "post", "head", "options"]
    queryset = Punch.objects.select_related("staff")
    serializer_class = PunchSerializer
    filterset_fields = ["staff", "student", "device", "source", "pin"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "qr": [MANAGE]}

    def get_permissions(self):
        if self.action == "check_in":
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_serializer_class(self):
        return ManualPunchSerializer if self.action == "create" else PunchSerializer

    @extend_schema(tags=[TAG], summary="Enter a staff punch by hand (office)",
                   request=ManualPunchSerializer, responses={201: PunchSerializer})
    def create(self, request, *args, **kwargs):
        serializer = ManualPunchSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        staff = data["staff"]
        self.check_campus_allowed(staff.campus_id)
        punch, _ = services.record_punch(
            organization=staff.organization, campus=staff.campus, staff=staff,
            punched_at=data["punched_at"], direction=data["direction"], source=Source.MANUAL,
            marked_by=request.user, note=data["note"], dedupe_key=f"manual:{uuid.uuid4().hex}",
        )
        return Response(PunchSerializer(punch).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="A QR code for staff to check in with, at a campus",
                   request=StaffQRSerializer, responses={200: None})
    @action(detail=False, methods=["post"])
    def qr(self, request):
        serializer = StaffQRSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        self.check_campus_allowed(data["campus"].pk)
        token, expires_at = services.issue_staff_qr(**data)
        return Response({"token": token, "expires_at": expires_at})

    @extend_schema(tags=[TAG], summary="Check in or out by scanning the campus QR (staff)",
                   request=StaffScanSerializer, responses={201: PunchSerializer, 200: PunchSerializer})
    @action(detail=False, methods=["post"], url_path="check-in")
    def check_in(self, request):
        serializer = StaffScanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        punch, created = services.staff_scan(user=request.user, **serializer.validated_data)
        return Response(PunchSerializer(punch).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------
@_schema("attendance device")
class AttendanceDeviceViewSet(AttendanceViewSet):
    """Biometric readers. A generic device gets an API key once, on
    creation or rotation; store it on the device. A ZKTeco device is
    identified by its serial number — restrict it with ``allowed_ips``."""

    queryset = AttendanceDevice.objects.select_related("campus")
    serializer_class = DeviceSerializer
    filterset_fields = ["campus", "kind", "is_active"]
    required_permissions = {"default": [DEVICES]}

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        device = AttendanceDevice.objects.get(pk=response.data["id"])
        if device.kind == AttendanceDevice.Kind.GENERIC:
            from integrations.biometric.auth import set_new_key

            response.data["api_key"] = set_new_key(device)
            response.data["key_prefix"] = device.key_prefix
        return response

    @extend_schema(tags=[TAG], summary="Issue a new API key (generic devices)",
                   request=None, responses={200: None})
    @action(detail=True, methods=["post"], url_path="rotate-key")
    def rotate_key(self, request, pk=None):
        from integrations.biometric.auth import set_new_key

        device = self.get_object()
        if device.kind != AttendanceDevice.Kind.GENERIC:
            raise ConflictError("Only generic devices use API keys.", code="no_key")
        return Response({"api_key": set_new_key(device), "key_prefix": device.key_prefix})


@_schema("biometric identity")
class BiometricIdentityViewSet(AttendanceViewSet):
    """Who each user number (PIN) on the devices is. Mapping a PIN applies
    the punches it already sent."""

    queryset = BiometricIdentity.objects.select_related("staff", "student")
    serializer_class = BiometricIdentitySerializer
    filterset_fields = ["pin", "staff", "student"]
    required_permissions = {"default": [DEVICES]}

    def get_queryset(self):
        # The person decides the campus: staff or student.
        qs = super(CampusScopedMixin, self).get_queryset()
        campus_ids = campus_ids_with_permission(self.request.user, DEVICES)
        if campus_ids is None:
            return qs
        from django.db.models import Q

        return qs.filter(Q(staff__campus_id__in=campus_ids) | Q(student__campus_id__in=campus_ids))

    def campus_of(self, validated_data):
        person = validated_data.get("staff") or validated_data.get("student")
        return person.campus_id if person else None

    def perform_create(self, serializer):
        super().perform_create(serializer)
        services.apply_unmapped_punches(serializer.instance)
