from rest_framework import serializers

from core.common.serializers import ensure_unique_together
from core.organizations.models import Campus
from modules.academics.models import Room, Section, TeachingAssignment, Term
from modules.academics.serializers import TenantSerializer
from modules.staff.models import StaffMember

from .models import BellSchedule, LessonChange, Period, TimetableEntry, Weekday
from .services import entries_on


class BellScheduleSerializer(TenantSerializer):
    campus = serializers.PrimaryKeyRelatedField(queryset=Campus.objects.all())
    campus_name = serializers.CharField(source="campus.name", read_only=True)

    class Meta:
        model = BellSchedule
        fields = ["id", "organization", "campus", "campus_name", "name", "is_active",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def validate_campus(self, campus):
        self.own(campus, "campus")
        if self.instance is not None and campus.pk != self.instance.campus_id:
            raise serializers.ValidationError("A bell schedule cannot move to another campus.")
        return campus

    def validate(self, attrs):
        if "name" in attrs:
            attrs["name"] = attrs["name"].strip()
        ensure_unique_together(
            self, attrs, ["campus", "name"],
            {"name": "This campus already has a bell schedule with this name."},
        )
        return attrs


class PeriodSerializer(TenantSerializer):
    schedule = serializers.PrimaryKeyRelatedField(queryset=BellSchedule.objects.all())
    schedule_name = serializers.CharField(source="schedule.name", read_only=True)

    class Meta:
        model = Period
        fields = ["id", "schedule", "schedule_name", "name", "start_time", "end_time",
                  "is_break", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_schedule(self, schedule):
        self.own(schedule, "bell schedule")
        if self.instance is not None and schedule.pk != self.instance.schedule_id:
            raise serializers.ValidationError("A period cannot move to another bell schedule.")
        return schedule

    def validate(self, attrs):
        get = lambda field: self.current(attrs, field)  # noqa: E731
        schedule, start, end = get("schedule"), get("start_time"), get("end_time")
        if end <= start:
            raise serializers.ValidationError({"end_time": "Must be after the start time."})

        if self.instance is not None and self.instance.timetable_entries.exists():
            # Moving a period under scheduled lessons could create clashes
            # nobody checked; set up a new period and move the lessons instead.
            for field in ("start_time", "end_time", "is_break"):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError(
                        {field: "Cannot change: lessons are scheduled in this period."}
                    )

        others = Period.objects.filter(schedule=schedule)
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        clash = others.filter(start_time__lt=end, end_time__gt=start).first()
        if clash is not None:
            raise serializers.ValidationError(
                f"Overlaps {clash.name} ({clash.start_time:%H:%M}–{clash.end_time:%H:%M})."
            )

        if "name" in attrs:
            attrs["name"] = attrs["name"].strip()
        ensure_unique_together(
            self, attrs, ["schedule", "name"],
            {"name": "This bell schedule already has a period with this name."},
        )
        return attrs


class TimetableEntrySerializer(TenantSerializer):
    teaching_assignment = serializers.PrimaryKeyRelatedField(
        queryset=TeachingAssignment.objects.select_related("section__program", "teacher")
    )
    period = serializers.PrimaryKeyRelatedField(
        queryset=Period.objects.select_related("schedule"), required=False
    )
    room = serializers.PrimaryKeyRelatedField(
        queryset=Room.objects.all(), required=False, allow_null=True,
        help_text="Defaults to the section's home room when left out.",
    )
    term = serializers.PrimaryKeyRelatedField(
        queryset=Term.objects.all(), required=False, allow_null=True,
        help_text="Empty: the lesson runs the whole academic year.",
    )
    combine_with = serializers.PrimaryKeyRelatedField(
        queryset=TimetableEntry.objects.select_related(
            "period__schedule", "room", "term", "teaching_assignment__section"
        ),
        required=False, write_only=True,
        help_text="Join this lesson of another section as one combined class: same teacher, "
                  "subject, time and room. Day, period, room and term are taken from it.",
    )
    day_name = serializers.CharField(source="get_day_of_week_display", read_only=True)
    section = serializers.IntegerField(source="teaching_assignment.section_id", read_only=True)
    section_name = serializers.CharField(
        source="teaching_assignment.section.display_name", read_only=True
    )
    subject = serializers.IntegerField(source="teaching_assignment.subject_id", read_only=True)
    subject_name = serializers.CharField(source="teaching_assignment.subject.name", read_only=True)
    teacher = serializers.IntegerField(source="teaching_assignment.teacher_id", read_only=True)
    teacher_name = serializers.CharField(
        source="teaching_assignment.teacher.full_name", read_only=True
    )
    period_name = serializers.CharField(source="period.name", read_only=True)
    start_time = serializers.TimeField(source="period.start_time", read_only=True)
    end_time = serializers.TimeField(source="period.end_time", read_only=True)
    room_name = serializers.CharField(source="room.name", read_only=True, default=None)
    term_name = serializers.CharField(source="term.name", read_only=True, default=None)

    class Meta:
        model = TimetableEntry
        fields = ["id", "teaching_assignment", "day_of_week", "day_name", "period", "period_name",
                  "start_time", "end_time", "section", "section_name", "subject", "subject_name",
                  "teacher", "teacher_name", "room", "room_name", "term", "term_name",
                  "combine_with", "combined_group", "created_at", "updated_at"]
        read_only_fields = ["id", "combined_group", "created_at", "updated_at"]
        extra_kwargs = {"day_of_week": {"required": False}}

    def validate_teaching_assignment(self, assignment):
        self.own(assignment, "teaching assignment")
        if self.instance is not None and assignment.pk != self.instance.teaching_assignment_id:
            if assignment.section_id != self.instance.teaching_assignment.section_id:
                raise serializers.ValidationError(
                    "A lesson can only move to another assignment of the same section."
                )
            if self.instance.combined_group is not None:
                raise serializers.ValidationError(
                    "This is a combined class. Use hand-over to change its teacher for every section."
                )
        return assignment

    def validate_period(self, period):
        return self.own(period, "period")

    def validate_room(self, room):
        return self.own(room, "room")

    def validate_term(self, term):
        return self.own(term, "term")

    def validate_combine_with(self, target):
        self.own(target, "lesson")
        if self.instance is not None:
            raise serializers.ValidationError("Only a new lesson can join a combined class.")
        return target

    def _take_slot_from(self, target, attrs):
        """A lesson joining a combined class meets when and where it does."""
        assignment, other = attrs["teaching_assignment"], target.teaching_assignment
        if (assignment.teacher_id, assignment.subject_id) != (other.teacher_id, other.subject_id):
            raise serializers.ValidationError(
                {"combine_with": "A combined class has one teacher and one subject; this "
                                 "assignment's teacher or subject differs."}
            )
        section, other_section = assignment.section, other.section
        if section.campus_id != other_section.campus_id or \
                section.academic_year_id != other_section.academic_year_id:
            raise serializers.ValidationError(
                {"combine_with": "Combined sections must be at one campus, in one academic year."}
            )
        members = TimetableEntry.objects.filter(pk=target.pk)
        if target.combined_group is not None:
            members = TimetableEntry.objects.filter(combined_group=target.combined_group)
        if members.filter(teaching_assignment__section_id=section.pk).exists():
            raise serializers.ValidationError({"combine_with": "This section is already in that class."})
        slot = {"day_of_week": target.day_of_week, "period": target.period,
                "room": target.room, "term": target.term}
        for field, value in slot.items():
            if field in attrs and attrs[field] != value:
                raise serializers.ValidationError(
                    {field: "A combined class meets at one time and place; leave this out."}
                )
        attrs.update(slot)

    def validate(self, attrs):
        # Kept aside for the view, which puts both lessons in one group.
        self.combine_target = attrs.pop("combine_with", None)
        if self.combine_target is not None:
            self._take_slot_from(self.combine_target, attrs)

        get = lambda field: self.current(attrs, field)  # noqa: E731
        if self.instance is None:
            missing = {f: "This field is required." for f in ("day_of_week", "period") if f not in attrs}
            if missing:
                raise serializers.ValidationError(missing)
        assignment, period = get("teaching_assignment"), get("period")
        section = assignment.section

        # A new lesson goes in the section's home room unless told otherwise.
        if self.instance is None and "room" not in attrs:
            attrs["room"] = section.home_room
        room, term = get("room"), get("term")

        if "teaching_assignment" in attrs and assignment.teacher.status == StaffMember.Status.LEFT:
            raise serializers.ValidationError(
                {"teaching_assignment": "The teacher has left. Assign another teacher first."}
            )
        if period.is_break:
            raise serializers.ValidationError({"period": "Lessons can't be scheduled in a break."})
        if period.schedule.campus_id != section.campus_id:
            raise serializers.ValidationError(
                {"period": "The period belongs to another campus's bell schedule."}
            )
        if room is not None:
            if room.campus_id != section.campus_id:
                raise serializers.ValidationError({"room": "The room is at another campus."})
            if not room.is_active and "room" in attrs and self.combine_target is None:
                raise serializers.ValidationError({"room": "The room is not in use."})
        if term is not None and term.academic_year_id != section.academic_year_id:
            raise serializers.ValidationError(
                {"term": "The term belongs to another academic year than the section."}
            )

        ensure_unique_together(
            self, attrs, ["teaching_assignment", "day_of_week", "period", "term"]
            if term is not None else ["teaching_assignment", "day_of_week", "period"],
            {"period": "This lesson is already scheduled then."},
            extra={} if term is not None else {"term__isnull": True},
        )
        return attrs


class LessonChangeSerializer(TenantSerializer):
    entry = serializers.PrimaryKeyRelatedField(
        queryset=TimetableEntry.objects.select_related(
            "period", "teaching_assignment__section", "teaching_assignment__teacher"
        )
    )
    substitute_teacher = serializers.PrimaryKeyRelatedField(
        queryset=StaffMember.objects.all(), required=False, allow_null=True
    )
    room = serializers.PrimaryKeyRelatedField(queryset=Room.objects.all(), required=False, allow_null=True)
    section_name = serializers.CharField(source="entry.teaching_assignment.section.display_name", read_only=True)
    subject_name = serializers.CharField(source="entry.teaching_assignment.subject.name", read_only=True)
    regular_teacher_name = serializers.CharField(
        source="entry.teaching_assignment.teacher.full_name", read_only=True
    )
    substitute_teacher_name = serializers.CharField(
        source="substitute_teacher.full_name", read_only=True, default=None
    )
    room_name = serializers.CharField(source="room.name", read_only=True, default=None)
    period_name = serializers.CharField(source="entry.period.name", read_only=True)
    start_time = serializers.TimeField(source="entry.period.start_time", read_only=True)
    end_time = serializers.TimeField(source="entry.period.end_time", read_only=True)

    class Meta:
        model = LessonChange
        fields = ["id", "entry", "date", "is_cancelled", "substitute_teacher", "substitute_teacher_name",
                  "room", "room_name", "note", "section_name", "subject_name", "regular_teacher_name",
                  "period_name", "start_time", "end_time", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_entry(self, entry):
        self.own(entry, "lesson")
        if self.instance is not None and entry.pk != self.instance.entry_id:
            raise serializers.ValidationError("A change can't move to another lesson.")
        return entry

    def validate_date(self, value):
        if self.instance is not None and value != self.instance.date:
            raise serializers.ValidationError("A change can't move to another date.")
        return value

    def validate_substitute_teacher(self, teacher):
        self.own(teacher, "staff member")
        if teacher is not None and teacher.status == StaffMember.Status.LEFT:
            raise serializers.ValidationError("This staff member has left.")
        return teacher

    def validate_room(self, room):
        return self.own(room, "room")

    def validate(self, attrs):
        get = lambda field: self.current(attrs, field)  # noqa: E731
        entry, day = get("entry"), get("date")
        cancelled, teacher, room = get("is_cancelled"), get("substitute_teacher"), get("room")
        section = entry.teaching_assignment.section

        if not entries_on(day, TimetableEntry.objects.filter(pk=entry.pk)).exists():
            raise serializers.ValidationError({"date": "The lesson doesn't take place on that date."})
        if cancelled and (teacher is not None or room is not None):
            raise serializers.ValidationError(
                "A cancelled lesson has no substitute or room; leave those empty."
            )
        if not cancelled and teacher is None and room is None:
            raise serializers.ValidationError(
                "Give a substitute teacher, another room, or cancel the lesson."
            )
        if teacher is not None and teacher.pk == entry.teaching_assignment.teacher_id:
            raise serializers.ValidationError(
                {"substitute_teacher": "This is the lesson's own teacher."}
            )
        if room is not None:
            if room.campus_id != section.campus_id:
                raise serializers.ValidationError({"room": "The room is at another campus."})
            if not room.is_active:
                raise serializers.ValidationError({"room": "The room is not in use."})
        ensure_unique_together(
            self, attrs, ["entry", "date"],
            {"date": "This lesson already has a change on that date. Edit that one."},
        )
        return attrs


class HandOverSerializer(serializers.Serializer):
    teaching_assignments = serializers.PrimaryKeyRelatedField(
        queryset=TeachingAssignment.objects.select_related("section", "teacher"), many=True,
        help_text="The assignments whose lessons move. Include every section of a combined class.",
    )
    teacher = serializers.PrimaryKeyRelatedField(queryset=StaffMember.objects.all())

    def validate_teacher(self, teacher):
        if teacher.organization_id != self.context["organization_id"]:
            raise serializers.ValidationError("Unknown staff member.")
        if teacher.status == StaffMember.Status.LEFT:
            raise serializers.ValidationError("This staff member has left.")
        return teacher

    def validate_teaching_assignments(self, assignments):
        if not assignments:
            raise serializers.ValidationError("Give at least one assignment.")
        if any(a.organization_id != self.context["organization_id"] for a in assignments):
            raise serializers.ValidationError("Unknown teaching assignment.")
        return list({a.pk: a for a in assignments}.values())

    def validate(self, attrs):
        already = [a for a in attrs["teaching_assignments"] if a.teacher_id == attrs["teacher"].pk]
        if already:
            raise serializers.ValidationError(
                {"teacher": f"Already teaches {len(already)} of these assignments."}
            )
        return attrs


class GenerateSerializer(serializers.Serializer):
    sections = serializers.PrimaryKeyRelatedField(
        queryset=Section.objects.select_related("program", "home_room"), many=True
    )
    schedule = serializers.PrimaryKeyRelatedField(queryset=BellSchedule.objects.all())
    days = serializers.ListField(
        child=serializers.ChoiceField(choices=Weekday.choices),
        help_text="Teaching days, ISO numbers: 1 = Monday … 7 = Sunday. Sunday–Friday is [7, 1, 2, 3, 4, 5].",
    )
    term = serializers.PrimaryKeyRelatedField(
        queryset=Term.objects.all(), required=False, allow_null=True,
        help_text="Generate lessons for this term only. Empty: all year.",
    )
    dry_run = serializers.BooleanField(
        default=True, help_text="True (default): only show the plan. False: save it."
    )

    def validate(self, attrs):
        org = self.context["organization_id"]
        sections, schedule, term = attrs["sections"], attrs["schedule"], attrs.get("term")
        if not sections:
            raise serializers.ValidationError({"sections": "Give at least one section."})
        if any(s.organization_id != org for s in sections):
            raise serializers.ValidationError({"sections": "Unknown section."})
        if schedule.organization_id != org:
            raise serializers.ValidationError({"schedule": "Unknown bell schedule."})
        if term is not None and term.organization_id != org:
            raise serializers.ValidationError({"term": "Unknown term."})
        if any(s.campus_id != schedule.campus_id for s in sections):
            raise serializers.ValidationError(
                {"sections": "Every section must be at the bell schedule's campus."}
            )
        if term is not None and any(s.academic_year_id != term.academic_year_id for s in sections):
            raise serializers.ValidationError({"term": "The term belongs to another academic year."})
        if not attrs["days"] or len(set(attrs["days"])) != len(attrs["days"]):
            raise serializers.ValidationError({"days": "Give each teaching day once."})
        attrs["sections"] = list({s.pk: s for s in sections}.values())
        return attrs


class LessonSerializer(serializers.Serializer):
    """One lesson as it happens on a date, after that day's change."""

    entry = serializers.IntegerField(source="entry.pk")
    date = serializers.DateField()
    day_of_week = serializers.IntegerField(source="entry.day_of_week")
    period = serializers.IntegerField(source="entry.period_id")
    period_name = serializers.CharField(source="entry.period.name")
    start_time = serializers.TimeField(source="entry.period.start_time")
    end_time = serializers.TimeField(source="entry.period.end_time")
    section = serializers.IntegerField(source="entry.teaching_assignment.section_id")
    section_name = serializers.CharField(source="entry.teaching_assignment.section.display_name")
    subject = serializers.IntegerField(source="entry.teaching_assignment.subject_id")
    subject_name = serializers.CharField(source="entry.teaching_assignment.subject.name")
    teacher = serializers.IntegerField(source="teacher.pk")
    teacher_name = serializers.CharField(source="teacher.full_name")
    regular_teacher = serializers.IntegerField(source="entry.teaching_assignment.teacher_id")
    room = serializers.IntegerField(source="room.pk", default=None)
    room_name = serializers.CharField(source="room.name", default=None)
    is_cancelled = serializers.BooleanField()
    change = serializers.IntegerField(source="change.pk", default=None)
    note = serializers.CharField(source="change.note", default="")
    combined_group = serializers.UUIDField(source="entry.combined_group")
