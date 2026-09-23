import uuid

from django.db import transaction
from django.utils.dateparse import parse_date
from django_filters import rest_framework as filters
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.audit.services import log_create, log_update, snapshot
from core.common.exceptions import ConflictError
from core.common.mixins import CampusScopedViewSet
from core.permissions.selectors import campus_ids_with_permission
from modules.academics.models import CurriculumSubject
from modules.academics.selectors import chosen_elective_ids
from modules.academics.serializers import TeachingAssignmentSerializer
from modules.parents.selectors import links_for_parent, parent_for_user
from modules.staff.selectors import staff_member_for_user
from modules.students.selectors import get_current_enrollment, student_for_user

from .generator import plan_timetable
from .models import BellSchedule, LessonChange, Period, TimetableEntry
from .serializers import (
    BellScheduleSerializer,
    GenerateSerializer,
    HandOverSerializer,
    LessonChangeSerializer,
    LessonSerializer,
    PeriodSerializer,
    TimetableEntrySerializer,
)
from .services import (
    ENTRY_RELATED,
    entries_on,
    find_date_clashes,
    hand_over,
    lessons_on,
    lock,
    lock_and_check,
    raise_if_clashes,
)

VIEW = "timetable.view"
MANAGE = "timetable.manage"


def _perms(**extra) -> dict:
    return {
        "list": [VIEW], "retrieve": [VIEW],
        "create": [MANAGE], "update": [MANAGE], "partial_update": [MANAGE], "destroy": [MANAGE],
        **extra,
    }


def _schema(noun: str):
    tag = "timetable"
    return extend_schema_view(
        list=extend_schema(tags=[tag], summary=f"List {noun}s"),
        retrieve=extend_schema(tags=[tag], summary=f"Retrieve a {noun}"),
        create=extend_schema(tags=[tag], summary=f"Create a {noun}"),
        update=extend_schema(tags=[tag], summary=f"Replace a {noun}"),
        partial_update=extend_schema(tags=[tag], summary=f"Update a {noun}"),
        destroy=extend_schema(tags=[tag], summary=f"Delete a {noun}"),
    )


def _query_date(request, required=True):
    raw = request.query_params.get("date")
    if raw is None and not required:
        return None
    value = parse_date(raw or "")
    if value is None:
        raise ValidationError({"date": "Give a date as YYYY-MM-DD."})
    return value


def _query_id(request, name):
    raw = request.query_params.get(name)
    if raw is None:
        return None
    if not raw.isdigit():
        raise ValidationError({name: "Must be an id."})
    return int(raw)


class TimetableViewSet(CampusScopedViewSet):
    audit_module = "timetable"
    required_permissions = _perms()

    def visible_campus_ids(self):
        return campus_ids_with_permission(self.request.user, VIEW)


@_schema("bell schedule")
class BellScheduleViewSet(TimetableViewSet):
    queryset = BellSchedule.objects.select_related("campus")
    serializer_class = BellScheduleSerializer
    filterset_fields = ["campus", "is_active"]
    search_fields = ["name"]
    ordering_fields = ["name"]

    def perform_destroy(self, instance):
        if TimetableEntry.objects.filter(period__schedule=instance).exists():
            raise ConflictError(
                "Lessons are scheduled in this bell schedule's periods. Remove those first.",
                code="in_use",
            )
        with transaction.atomic():
            for period in instance.periods.all():
                period.delete(deleted_by=self.request.user)
            super().perform_destroy(instance)


@_schema("period")
class PeriodViewSet(TimetableViewSet):
    """A bell schedule's periods. ``?schedule=<id>`` lists one schedule's day."""

    queryset = Period.objects.select_related("schedule")
    serializer_class = PeriodSerializer
    campus_field = "schedule__campus"
    filterset_fields = ["schedule", "schedule__campus", "is_break"]
    ordering_fields = ["start_time"]
    ordering = ["schedule", "start_time", "pk"]

    def campus_of(self, validated_data):
        schedule = validated_data.get("schedule")
        return schedule.campus if schedule is not None else None

    def perform_destroy(self, instance):
        if instance.timetable_entries.exists():
            raise ConflictError(
                "Lessons are scheduled in this period. Remove those first.", code="in_use"
            )
        super().perform_destroy(instance)


class TimetableEntryFilter(filters.FilterSet):
    section = filters.NumberFilter(field_name="teaching_assignment__section")
    teacher = filters.NumberFilter(field_name="teaching_assignment__teacher")
    subject = filters.NumberFilter(field_name="teaching_assignment__subject")
    campus = filters.NumberFilter(field_name="teaching_assignment__section__campus")
    academic_year = filters.NumberFilter(field_name="teaching_assignment__section__academic_year")
    date = filters.DateFilter(
        method="filter_date",
        label="Lessons that take place on this date: its weekday, within the "
              "section's academic year and, for term lessons, within the term.",
    )

    class Meta:
        model = TimetableEntry
        fields = ["teaching_assignment", "day_of_week", "period", "room", "term", "combined_group"]

    def filter_date(self, queryset, name, value):
        return entries_on(value, queryset)


_LESSON_FILTERS = [
    OpenApiParameter("date", str, required=True, description="YYYY-MM-DD"),
    OpenApiParameter("section", int), OpenApiParameter("teacher", int), OpenApiParameter("room", int),
]


@_schema("timetable entry")
class TimetableEntryViewSet(TimetableViewSet):
    """The weekly timetable. Filter by ``section``, ``teacher`` or ``room``
    for one class's, one teacher's or one room's week; ``date`` gives the
    lessons of one day.

    A write that would double-book a teacher, room or section is refused
    with 409 ``timetable_clash``; ``details.clashes`` lists what it hit.
    """

    queryset = TimetableEntry.objects.select_related(*ENTRY_RELATED)
    serializer_class = TimetableEntrySerializer
    campus_field = "teaching_assignment__section__campus"
    filterset_class = TimetableEntryFilter
    ordering_fields = ["day_of_week", "period__start_time"]
    ordering = ["day_of_week", "period__start_time", "pk"]
    required_permissions = _perms(
        day=[VIEW],
        hand_over=[MANAGE, "academics.manage_classes"],
        generate=[MANAGE],
    )
    SLOT_FIELDS = ("day_of_week", "period", "room", "term")

    def get_permissions(self):
        # Teachers, students and parents read their own timetable without
        # any timetable permission, like the other /me/ endpoints.
        if self.action == "me":
            return [IsAuthenticated()]
        return super().get_permissions()

    def campus_of(self, validated_data):
        assignment = validated_data.get("teaching_assignment")
        return assignment.section.campus if assignment is not None else None

    # -- writes ------------------------------------------------------------
    def _check(self, serializer, *, group=None, exclude_pks=()):
        data, instance = serializer.validated_data, serializer.instance
        current = lambda field: data[field] if field in data else getattr(instance, field, None)  # noqa: E731
        # Campus first: a write to a campus the caller doesn't run is a 403,
        # never a 409 describing that campus's timetable.
        self.check_campus_allowed(self.campus_of(data))
        lock_and_check(
            organization_id=instance.organization_id if instance else self.get_target_organization_id(),
            assignment=current("teaching_assignment"),
            day_of_week=current("day_of_week"),
            period=current("period"),
            room=current("room"),
            term=current("term"),
            exclude_pks=[*exclude_pks, *([instance.pk] if instance else [])],
            group=group,
            visible_campus_ids=self.visible_campus_ids(),
        )

    def perform_create(self, serializer):
        target = serializer.combine_target
        with transaction.atomic():
            if target is None:
                self._check(serializer)
                super().perform_create(serializer)
                return
            # Group the target first so the check sees it as the same class;
            # a clash rolls this back with everything else.
            if target.combined_group is None:
                before = snapshot(target)
                target.combined_group = uuid.uuid4()
                target.save(update_fields=["combined_group", "updated_at"])
                log_update(self.request, target, before=before, module=self.audit_module)
            self._check(serializer, group=target.combined_group)
            serializer.validated_data["combined_group"] = target.combined_group
            super().perform_create(serializer)

    def perform_update(self, serializer):
        instance, data = serializer.instance, serializer.validated_data
        group = instance.combined_group
        moves = any(f in data and data[f] != getattr(instance, f) for f in self.SLOT_FIELDS)
        with transaction.atomic():
            if group is None or not moves:
                self._check(serializer, group=group)
                super().perform_update(serializer)
                return
            # A combined class moves together: every section's lesson goes to
            # the new time and room, and each is checked there.
            members = list(TimetableEntry.objects.filter(combined_group=group).exclude(pk=instance.pk)
                           .select_related(*ENTRY_RELATED))
            member_pks = [m.pk for m in members]
            self._check(serializer, group=group, exclude_pks=member_pks)
            slot = {f: data[f] if f in data else getattr(instance, f) for f in self.SLOT_FIELDS}
            for member in members:
                lock_and_check(
                    organization_id=member.organization_id, assignment=member.teaching_assignment,
                    exclude_pks=[instance.pk, *member_pks], group=group,
                    visible_campus_ids=self.visible_campus_ids(), **slot,
                )
            super().perform_update(serializer)
            for member in members:
                before = snapshot(member)
                for field, value in slot.items():
                    setattr(member, field, value)
                member.save(update_fields=[*self.SLOT_FIELDS, "updated_at"])
                log_update(self.request, member, before=before, module=self.audit_module)

    def perform_destroy(self, instance):
        group = instance.combined_group
        with transaction.atomic():
            super().perform_destroy(instance)
            if group is not None:
                rest = list(TimetableEntry.objects.filter(combined_group=group))
                if len(rest) == 1:
                    # One section left: no longer a combined class.
                    rest[0].combined_group = None
                    rest[0].save(update_fields=["combined_group", "updated_at"])

    # -- actions -----------------------------------------------------------
    @extend_schema(tags=["timetable"], summary="The lessons of one day, with that day's changes",
                   description="Substitutes, room changes and cancellations applied. `teacher` "
                               "includes lessons they cover; `room` includes lessons moved into it.",
                   parameters=_LESSON_FILTERS, responses={200: LessonSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def day(self, request):
        day = _query_date(request)
        lessons = lessons_on(
            day, organization_id=request.user.organization_id, entries=self.get_queryset(),
            section=_query_id(request, "section"), teacher=_query_id(request, "teacher"),
            room=_query_id(request, "room"),
        )
        return Response(LessonSerializer(lessons, many=True).data)

    @extend_schema(
        tags=["timetable"], summary="Hand lessons over to another teacher",
        description="Moves every lesson of the given assignments to the teacher, creating their "
                    "assignments as needed. All or nothing: 409 timetable_clash lists every clash.",
        request=HandOverSerializer, responses={200: TeachingAssignmentSerializer(many=True)},
    )
    @action(detail=False, methods=["post"], url_path="hand-over")
    def hand_over(self, request):
        serializer = HandOverSerializer(
            data=request.data, context={"organization_id": self.get_target_organization_id()}
        )
        serializer.is_valid(raise_exception=True)
        assignments = serializer.validated_data["teaching_assignments"]
        for assignment in assignments:
            self.check_campus_allowed(assignment.section.campus_id)
        with transaction.atomic():
            new = hand_over(assignments=assignments, teacher=serializer.validated_data["teacher"],
                            visible_campus_ids=self.visible_campus_ids(), by=request.user)
        return Response(TeachingAssignmentSerializer(new, many=True).data)

    @extend_schema(
        tags=["timetable"], summary="Generate the weekly timetable",
        description="Fills each teaching assignment up to its periods_per_week. With dry_run "
                    "(the default) it only returns the plan; with dry_run false it saves it.",
        request=GenerateSerializer, responses={200: None, 201: None},
    )
    @action(detail=False, methods=["post"])
    def generate(self, request):
        serializer = GenerateSerializer(
            data=request.data, context={"organization_id": self.get_target_organization_id()}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        self.check_campus_allowed(data["schedule"].campus_id)
        plan = plan_timetable(sections=data["sections"], schedule=data["schedule"],
                              days=data["days"], term=data.get("term"))

        created = []
        if not data["dry_run"]:
            with transaction.atomic():
                for proposal in plan.proposals:
                    # The plan was made without locks; check each lesson
                    # again under them, as a hand-made one would be.
                    lock_and_check(
                        organization_id=proposal.assignment.organization_id,
                        assignment=proposal.assignment, day_of_week=proposal.day,
                        period=proposal.period, room=proposal.room, term=data.get("term"),
                        visible_campus_ids=self.visible_campus_ids(),
                    )
                    entry = TimetableEntry.objects.create(
                        organization_id=proposal.assignment.organization_id,
                        teaching_assignment=proposal.assignment, day_of_week=proposal.day,
                        period=proposal.period, room=proposal.room, term=data.get("term"),
                    )
                    log_create(request, entry, module=self.audit_module)
                    created.append(entry.pk)

        lessons = [
            {
                "id": created[i] if created else None,
                "teaching_assignment": p.assignment.pk,
                "section": p.assignment.section_id,
                "section_name": p.assignment.section.display_name,
                "subject_name": p.assignment.subject.name,
                "teacher_name": p.assignment.teacher.full_name,
                "day_of_week": p.day,
                "period": p.period.pk,
                "period_name": p.period.name,
                "start_time": p.period.start_time.strftime("%H:%M"),
                "end_time": p.period.end_time.strftime("%H:%M"),
                "room": p.room.pk if p.room else None,
                "room_name": p.room.name if p.room else None,
            }
            for i, p in enumerate(plan.proposals)
        ]
        return Response(
            {"dry_run": data["dry_run"], "created": len(created), "lessons": lessons,
             "unplaced": plan.unplaced},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["timetable"], summary="My timetable",
        description="For a teacher, their lessons (and the ones they cover on a date). For a "
                    "student, their class's lessons: compulsory subjects and the electives they "
                    "take (every elective while none is recorded). For a parent, a child's, "
                    "chosen with `student` when there is more than one. Without `date`, the "
                    "week's lessons; with it, that day's, changes applied.",
        parameters=[
            OpenApiParameter("date", str, description="YYYY-MM-DD"),
            OpenApiParameter("as", str, enum=["teacher", "student", "parent"],
                             description="Which of your profiles, when you have several."),
            OpenApiParameter("student", int, description="Parents: which child."),
        ],
        responses={200: None},
    )
    @action(detail=False, methods=["get"])
    def me(self, request):
        day = _query_date(request, required=False)
        wanted = request.query_params.get("as")
        user = request.user
        if wanted not in (None, "teacher", "student", "parent"):
            raise ValidationError({"as": "One of teacher, student, parent."})

        # The first profile the account has, in this order, unless ``as`` picks one.
        finders = {"teacher": staff_member_for_user, "student": student_for_user,
                   "parent": parent_for_user}
        role, profile = None, None
        for name, find in finders.items():
            if wanted in (None, name):
                profile = find(user)
                if profile is not None:
                    role = name
                    break
        if role is None:
            raise NotFound("No teacher, student or parent profile is linked to your account.")

        base = TimetableEntry.objects.filter(organization_id=user.organization_id)
        body = {"as": role, "date": day.isoformat() if day else None}

        if role == "teacher":
            if day is not None:
                lessons = lessons_on(day, organization_id=user.organization_id, teacher=profile, entries=base)
                return Response({**body, "lessons": LessonSerializer(lessons, many=True).data})
            return Response({**body, "lessons": self._week(base.filter(teaching_assignment__teacher=profile))})

        student = profile if role == "student" else self._child(profile)
        body["student"] = student.pk
        enrollment = get_current_enrollment(student)
        if enrollment is None or enrollment.section_id is None:
            return Response({**body, "lessons": []})
        section = enrollment.section
        entries = base.filter(teaching_assignment__section=section)
        chosen = chosen_elective_ids(enrollment)
        if chosen:
            others = CurriculumSubject.objects.filter(
                program_id=section.program_id, level=section.level, is_elective=True
            ).exclude(subject_id__in=chosen).values("subject_id")
            entries = entries.exclude(teaching_assignment__subject_id__in=others)
        if day is not None:
            lessons = lessons_on(day, organization_id=user.organization_id, section=section, entries=entries)
            return Response({**body, "lessons": LessonSerializer(lessons, many=True).data})
        return Response({**body, "lessons": self._week(entries)})

    def _week(self, entries):
        entries = entries.select_related(*ENTRY_RELATED).order_by("day_of_week", "period__start_time", "pk")
        return TimetableEntrySerializer(entries, many=True, context=self.get_serializer_context()).data

    def _child(self, parent):
        links = list(links_for_parent(parent))
        wanted = _query_id(self.request, "student")
        if wanted is not None:
            links = [link for link in links if link.student_id == wanted]
            if not links:
                raise NotFound("That student isn't linked to you.")
        elif len(links) > 1:
            raise ValidationError({"student": "You have several children linked; choose one."})
        if not links:
            raise NotFound("No student is linked to you.")
        return links[0].student


class LessonChangeFilter(filters.FilterSet):
    date_from = filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="date", lookup_expr="lte")
    section = filters.NumberFilter(field_name="entry__teaching_assignment__section")
    teacher = filters.NumberFilter(field_name="entry__teaching_assignment__teacher",
                                   label="The lesson's regular teacher.")

    class Meta:
        model = LessonChange
        fields = ["entry", "date", "substitute_teacher", "room", "is_cancelled"]


@_schema("lesson change")
class LessonChangeViewSet(TimetableViewSet):
    """One lesson on one date: a substitute teacher, another room, or
    cancelled. The substitute or room must be free then (409 otherwise)."""

    queryset = LessonChange.objects.select_related(
        "entry__period", "entry__teaching_assignment__section__program",
        "entry__teaching_assignment__subject", "entry__teaching_assignment__teacher",
        "substitute_teacher", "room",
    )
    serializer_class = LessonChangeSerializer
    campus_field = "entry__teaching_assignment__section__campus"
    filterset_class = LessonChangeFilter
    ordering_fields = ["date"]
    ordering = ["date", "entry__period__start_time", "pk"]

    def campus_of(self, validated_data):
        entry = validated_data.get("entry")
        return entry.teaching_assignment.section.campus_id if entry is not None else None

    def _check(self, serializer):
        data, instance = serializer.validated_data, serializer.instance
        current = lambda field: data[field] if field in data else getattr(instance, field, None)  # noqa: E731
        entry = current("entry")
        self.check_campus_allowed(entry.teaching_assignment.section.campus_id)
        teacher, room = current("substitute_teacher"), current("room")
        lock(teachers=[teacher.pk] if teacher else [], rooms=[room.pk] if room else [])
        raise_if_clashes(
            find_date_clashes(organization_id=entry.organization_id, entry=entry, day=current("date"),
                              teacher=teacher, room=room),
            self.visible_campus_ids(),
        )

    def perform_create(self, serializer):
        with transaction.atomic():
            self._check(serializer)
            super().perform_create(serializer)

    def perform_update(self, serializer):
        with transaction.atomic():
            self._check(serializer)
            super().perform_update(serializer)
