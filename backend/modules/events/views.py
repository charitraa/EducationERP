from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.common.exceptions import ConflictError
from core.common.mixins import CampusScopedViewSet, OrganizationScopedViewSet
from core.common.permissions import HasPermission
from core.permissions.selectors import campus_ids_with_permission
from modules.parents.selectors import links_for_parent, parent_for_user
from modules.students.selectors import student_for_user

from . import selectors, services
from .models import (
    Award,
    AwardRule,
    Event,
    EventCategory,
    EventRegistration,
    EventStatus,
    PointEntry,
    PointRule,
    RegistrationStatus,
    StudentAward,
    StudentPoints,
)
from .serializers import (
    AwardPointsSerializer,
    AwardRuleSerializer,
    AwardSerializer,
    CancelEventSerializer,
    DecideRegistrationSerializer,
    EventAttendanceSerializer,
    EventCategorySerializer,
    EventParticipationSerializer,
    EventRegistrationSerializer,
    EventSerializer,
    GrantAwardSerializer,
    MarkAttendanceSerializer,
    PointEntrySerializer,
    PointRuleSerializer,
    RegisterSerializer,
    RegisterStudentSerializer,
    StudentAwardSerializer,
    StudentPointsSerializer,
)
from .services import COORDINATE, MANAGE, VIEW

TAG = "events"


def _schema(noun: str, *actions):
    summaries = {"list": f"List {noun}s", "retrieve": f"Retrieve a {noun}",
                 "create": f"Create a {noun}", "update": f"Replace a {noun}",
                 "partial_update": f"Update a {noun}", "destroy": f"Delete a {noun}"}
    return extend_schema_view(**{a: extend_schema(tags=[TAG], summary=summaries[a])
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


class EventsViewSet(CampusScopedViewSet):
    audit_module = "events"


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
@_schema("event category")
class EventCategoryViewSet(OrganizationScopedViewSet):
    queryset = EventCategory.objects.all()
    serializer_class = EventCategorySerializer
    audit_module = "events"
    filterset_fields = ["is_active"]
    search_fields = ["name", "code"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "update": [MANAGE],
                            "partial_update": [MANAGE], "destroy": [MANAGE]}

    def perform_destroy(self, instance):
        if instance.events.exists() or PointRule.objects.filter(category=instance).exists() or \
                AwardRule.objects.filter(category=instance).exists():
            raise ConflictError("This category is used by an event or a rule.", code="in_use")
        super().perform_destroy(instance)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
class EventFilter(filters.FilterSet):
    date_from = filters.DateFilter(field_name="start_at", lookup_expr="date__gte")
    date_to = filters.DateFilter(field_name="start_at", lookup_expr="date__lte")

    class Meta:
        model = Event
        fields = ["campus", "category", "status", "registration_mode", "organized_by"]


@_schema("event")
class EventViewSet(OrganizationScopedViewSet):
    queryset = Event.objects.select_related("category", "campus", "organized_by")
    serializer_class = EventSerializer
    filterset_class = EventFilter
    search_fields = ["name", "venue"]
    ordering_fields = ["start_at", "created_at"]
    required_permissions = {
        "list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "update": [MANAGE], "partial_update": [MANAGE],
        "destroy": [MANAGE], "publish": [MANAGE], "cancel": [MANAGE], "registrations": [VIEW],
        "mark_attendance": [COORDINATE], "attendance": [VIEW],
        "participation": [VIEW], "record_participation": [COORDINATE], "roster": [VIEW],
    }

    def get_permissions(self):
        # "register" (any student) and "me" bypass the permission-code system entirely — see below.
        if self.action in ("me", "register"):
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        # Not CampusScopedMixin: its plain campus__in filter would silently exclude every
        # org-wide event (campus=None), since SQL's IN never matches NULL. An event with no
        # campus is shared by every campus-scoped role instead — the same rule
        # CalendarEventViewSet applies to the academic calendar's own shared events.
        from django.db.models import Q

        qs = super().get_queryset()
        for code in HasPermission().get_required_permissions(self.request, self):
            campus_ids = campus_ids_with_permission(self.request.user, code)
            if campus_ids is not None:
                qs = qs.filter(Q(campus_id__in=campus_ids) | Q(campus__isnull=True))
                if self.action not in ("list", "retrieve"):
                    qs = qs.filter(campus__isnull=False)
        return qs

    def _check_scope(self, campus):
        from rest_framework.exceptions import PermissionDenied

        campus_ids = campus_ids_with_permission(self.request.user, MANAGE)
        if campus_ids is None:
            return
        if campus is None:
            raise PermissionDenied("Only an organization-wide role can add an event for every campus.")
        if campus.pk not in campus_ids:
            raise PermissionDenied("Your role does not cover this campus for this action.")

    def perform_create(self, serializer):
        self._check_scope(serializer.validated_data.get("campus"))
        super().perform_create(serializer)

    def perform_destroy(self, instance):
        # Soft delete keeps the registrations, attendance, placings and the
        # points they earned, but hides the event they belong to. Once it has
        # been published, cancel it instead, as with an exam.
        if instance.status != EventStatus.DRAFT:
            raise ConflictError("Only an event being set up can be deleted. Cancel it instead.", code="not_draft")
        super().perform_destroy(instance)

    def _event(self):
        return self.get_object()

    @extend_schema(tags=[TAG], summary="Publish the event", request=None, responses={200: EventSerializer})
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        return Response(EventSerializer(services.publish_event(self._event(), by=request.user)).data)

    @extend_schema(tags=[TAG], summary="Cancel the event", request=CancelEventSerializer,
                   responses={200: EventSerializer})
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        serializer = CancelEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = services.cancel_event(self._event(), serializer.validated_data["reason"], by=request.user)
        return Response(EventSerializer(event).data)

    @extend_schema(tags=[TAG], summary="Register for the event (students)", request=RegisterSerializer,
                   responses={201: EventRegistrationSerializer})
    @action(detail=True, methods=["post"])
    def register(self, request, pk=None):
        from django.db.models import Q

        student = student_for_user(request.user)
        if student is None:
            from core.common.exceptions import PermissionDeniedError

            raise PermissionDeniedError("Only a student account can register for an event.", code="not_a_student")
        # Not through the shared, role-permission-driven queryset: a student's own campus (or a
        # shared, every-campus event) decides what they may even see here, not events.* codes.
        event = Event.objects.filter(
            Q(campus__isnull=True) | Q(campus_id=student.campus_id),
            organization_id=request.user.organization_id, pk=pk,
        ).first()
        if event is None:
            raise NotFound("No such event.")
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration = services.register(event, student, note=serializer.validated_data["note"], by=request.user)
        return Response(EventRegistrationSerializer(registration).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Who's registered", responses={200: EventRegistrationSerializer(many=True)})
    @action(detail=True, methods=["get"])
    def registrations(self, request, pk=None):
        event = self._event()
        rows = event.registrations.exclude(status=RegistrationStatus.WITHDRAWN).select_related("student")
        return Response(EventRegistrationSerializer(rows, many=True).data)

    @extend_schema(tags=[TAG], summary="Mark attendance", request=MarkAttendanceSerializer,
                   responses={200: EventAttendanceSerializer(many=True)})
    @action(detail=True, methods=["post"], url_path="mark-attendance")
    def mark_attendance(self, request, pk=None):
        event = self._event()
        services.ensure_can_run(request.user, event)
        serializer = MarkAttendanceSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        entries = [(e["student"].pk, e["status"]) for e in serializer.validated_data["entries"]]
        records = services.mark_attendance(event, entries, by=request.user)
        return Response(EventAttendanceSerializer(records, many=True).data)

    @extend_schema(tags=[TAG], summary="Attendance taken so far", responses={200: EventAttendanceSerializer(many=True)})
    @action(detail=True, methods=["get"])
    def attendance(self, request, pk=None):
        event = self._event()
        return Response(EventAttendanceSerializer(event.attendance.select_related("student"), many=True).data)

    @extend_schema(tags=[TAG], summary="Record a student's role (and, for a competition, where they placed)",
                   request=EventParticipationSerializer, responses={201: EventParticipationSerializer})
    @action(detail=True, methods=["post"], url_path="record-participation")
    def record_participation(self, request, pk=None):
        event = self._event()
        services.ensure_can_run(request.user, event)
        serializer = EventParticipationSerializer(data={**request.data, "event": event.pk},
                                                  context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        participation = services.record_participation(
            event, data["student"], data["role"], position=data.get("position"), remark=data.get("remark", ""),
            by=request.user)
        return Response(EventParticipationSerializer(participation).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="Participation recorded so far",
                   responses={200: EventParticipationSerializer(many=True)})
    @action(detail=True, methods=["get"])
    def participation(self, request, pk=None):
        event = self._event()
        return Response(EventParticipationSerializer(event.participation.select_related("student"), many=True).data)

    @extend_schema(tags=[TAG], summary="Everyone tied to the event: registration, attendance, participation",
                   responses={200: None})
    @action(detail=True, methods=["get"])
    def roster(self, request, pk=None):
        event = self._event()
        return Response(selectors.event_roster(event))

    @extend_schema(tags=[TAG], summary="Events I can register for, or I'm registered for (students, parents)",
                   parameters=[OpenApiParameter("student", int, description="Parents: which child.")],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def me(self, request):
        from django.db.models import Q

        student = _own_student(request)
        events = Event.objects.filter(
            Q(campus__isnull=True) | Q(campus_id=student.campus_id),
            organization_id=request.user.organization_id, status="published",
        )
        registrations = {r.event_id: r for r in EventRegistration.objects.filter(student=student).exclude(
            status=RegistrationStatus.WITHDRAWN)}
        out = []
        for event in events.select_related("category").order_by("start_at"):
            registration = registrations.get(event.pk)
            out.append({**EventSerializer(event).data,
                       "my_registration": EventRegistrationSerializer(registration).data if registration else None})
        return Response(out)


# ---------------------------------------------------------------------------
# Registrations, attendance, participation (top-level, read/withdraw)
# ---------------------------------------------------------------------------
@_schema("registration", "list", "retrieve")
class EventRegistrationViewSet(OrganizationScopedViewSet):
    http_method_names = ["get", "post", "head", "options"]
    queryset = EventRegistration.objects.select_related("event", "student")
    serializer_class = EventRegistrationSerializer
    filterset_fields = ["event", "student", "status"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [COORDINATE], "decide": [COORDINATE]}

    def get_permissions(self):
        if self.action == "withdraw":
            return [IsAuthenticated()]
        return super().get_permissions()

    @extend_schema(tags=[TAG], summary="Register a student (organizer or events office)",
                   description="For a team sheet or a student without an account. The same rules as "
                               "self-registration (open, not full, not twice, the student's campus); an "
                               "approval event's registration is confirmed at once.",
                   request=RegisterStudentSerializer, responses={201: EventRegistrationSerializer})
    def create(self, request, *args, **kwargs):
        # Not ModelViewSet's create: everything goes through services.register_for, which
        # enforces capacity, the registration mode and duplicate sign-ups.
        serializer = RegisterStudentSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        registration = services.register_for(data["event"], data["student"], note=data["note"], by=request.user)
        return Response(EventRegistrationSerializer(registration).data, status=status.HTTP_201_CREATED)

    def get_queryset(self):
        # Same reasoning as EventViewSet: an org-wide event's registrations are shared by every
        # campus-scoped role, not excluded by an IN-list that never matches NULL.
        from django.db.models import Q

        qs = super().get_queryset()
        for code in HasPermission().get_required_permissions(self.request, self):
            campus_ids = campus_ids_with_permission(self.request.user, code)
            if campus_ids is not None:
                qs = qs.filter(Q(event__campus_id__in=campus_ids) | Q(event__campus__isnull=True))
        return qs

    @extend_schema(tags=[TAG], summary="Approve or reject a registration", request=DecideRegistrationSerializer,
                   responses={200: EventRegistrationSerializer})
    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        registration = self.get_object()
        services.ensure_can_run(request.user, registration.event)
        serializer = DecideRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        registration = services.decide_registration(registration, serializer.validated_data["approve"],
                                                    note=serializer.validated_data["note"], by=request.user)
        return Response(EventRegistrationSerializer(registration).data)

    @extend_schema(tags=[TAG], summary="Withdraw your registration (students)", request=None,
                   responses={200: EventRegistrationSerializer})
    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        registration = self.get_object()
        student = student_for_user(request.user)
        if student is None or registration.student_id != student.pk:
            from core.common.exceptions import PermissionDeniedError

            raise PermissionDeniedError("Only the student who registered can withdraw.", code="not_yours")
        registration = services.withdraw_registration(registration, by=request.user)
        return Response(EventRegistrationSerializer(registration).data)


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------
@_schema("point rule")
class PointRuleViewSet(OrganizationScopedViewSet):
    queryset = PointRule.objects.select_related("category")
    serializer_class = PointRuleSerializer
    audit_module = "events"
    filterset_fields = ["source", "category", "is_active"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "update": [MANAGE],
                            "partial_update": [MANAGE], "destroy": [MANAGE]}


@_schema("point entry", "list", "retrieve")
class PointEntryViewSet(EventsViewSet):
    http_method_names = ["get", "post", "head", "options"]
    queryset = PointEntry.objects.select_related("student", "rule", "event")
    serializer_class = PointEntrySerializer
    campus_field = "student__campus"
    filterset_fields = ["student", "event", "rule"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE]}

    def campus_of(self, validated_data):
        student = validated_data.get("student")
        return student.campus if student is not None else None

    @extend_schema(tags=[TAG], summary="Award points by hand", request=AwardPointsSerializer,
                   responses={201: PointEntrySerializer})
    def create(self, request, *args, **kwargs):
        serializer = AwardPointsSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        self.check_campus_allowed(data["student"].campus_id)
        entry = services.award_points(data["student"], data["points"], data["reason"], by=request.user)
        return Response(PointEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


@_schema("student points", "list", "retrieve")
class StudentPointsViewSet(EventsViewSet):
    http_method_names = ["get", "head", "options"]
    queryset = StudentPoints.objects.select_related("student")
    serializer_class = StudentPointsSerializer
    campus_field = "student__campus"
    lookup_field = "student_id"
    filterset_fields = ["student"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "leaderboard": [VIEW]}

    def get_permissions(self):
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    @extend_schema(tags=[TAG], summary="Students with the most points", responses={200: None})
    @action(detail=False, methods=["get"])
    def leaderboard(self, request):
        from core.permissions.selectors import campus_ids_with_permission

        campus_ids = campus_ids_with_permission(request.user, VIEW)
        return Response(selectors.leaderboard(request.user.organization_id, campus_ids))

    @extend_schema(tags=[TAG], summary="My points and awards (students), or a child's (parents)",
                   parameters=[OpenApiParameter("student", int, description="Parents: which child.")],
                   responses={200: None})
    @action(detail=False, methods=["get"])
    def me(self, request):
        return Response(selectors.student_summary(_own_student(request)))


# ---------------------------------------------------------------------------
# Awards
# ---------------------------------------------------------------------------
@_schema("award")
class AwardViewSet(OrganizationScopedViewSet):
    queryset = Award.objects.all()
    serializer_class = AwardSerializer
    audit_module = "events"
    filterset_fields = ["kind", "is_active"]
    search_fields = ["name", "code"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "update": [MANAGE],
                            "partial_update": [MANAGE], "destroy": [MANAGE]}

    def perform_destroy(self, instance):
        if instance.holders.exists():
            raise ConflictError("Students hold this award.", code="in_use")
        super().perform_destroy(instance)


@_schema("award rule")
class AwardRuleViewSet(OrganizationScopedViewSet):
    queryset = AwardRule.objects.select_related("award", "category")
    serializer_class = AwardRuleSerializer
    audit_module = "events"
    filterset_fields = ["award", "threshold_kind", "is_active"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "update": [MANAGE],
                            "partial_update": [MANAGE], "destroy": [MANAGE]}


@_schema("student award", "list", "retrieve", "create")
class StudentAwardViewSet(EventsViewSet):
    """Who holds which award. History, like a scholarship grant: ending one
    keeps the record — see the ``end`` action."""

    http_method_names = ["get", "post", "head", "options"]
    queryset = StudentAward.objects.select_related("student__campus", "award")
    serializer_class = StudentAwardSerializer
    campus_field = "student__campus"
    filterset_fields = ["student", "award"]
    required_permissions = {"list": [VIEW], "retrieve": [VIEW], "create": [MANAGE], "end": [MANAGE]}

    def campus_of(self, validated_data):
        student = validated_data.get("student")
        return student.campus if student is not None else None

    @extend_schema(tags=[TAG], summary="Grant an award by hand", request=GrantAwardSerializer,
                   responses={201: StudentAwardSerializer})
    def create(self, request, *args, **kwargs):
        serializer = GrantAwardSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        self.check_campus_allowed(data["student"].campus_id)
        grant = services.grant_award(data["student"], data["award"], note=data["note"], by=request.user)
        return Response(StudentAwardSerializer(grant).data, status=status.HTTP_201_CREATED)

    @extend_schema(tags=[TAG], summary="End a student's award (mainly for a title)", request=None,
                   responses={200: StudentAwardSerializer})
    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        grant = self.get_object()
        grant = services.end_award(grant, by=request.user)
        return Response(StudentAwardSerializer(grant).data)
