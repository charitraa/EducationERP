"""Clash detection and multi-row writes for the timetable.

Weekly lessons
--------------
A new or changed entry clashes with a live entry of the same academic year,
on the same weekday, whose period overlaps in clock time and whose term can
run at the same time (an all-year entry overlaps every term), when they share:

- the **teacher** — at any campus, a person can't be in two places;
- the **room**;
- the **section** — unless both subjects are electives at that level and no
  student of the section takes both: those run in parallel for different
  groups of students.

Entries of one combined class (same ``combined_group``) share their teacher
and room by design, so they never clash with each other.

A weekly lesson also clashes with an upcoming **substitution** (a lesson
change from today on) that already has the teacher or room busy then.

Lessons on a date
-----------------
A lesson change gives one lesson on one date a substitute teacher or another
room. It clashes with whatever that teacher or room is doing that day, after
that day's other changes are applied.

Race safety
-----------
The database can't express "overlapping times" portably, so writes lock the
section, teacher and room rows first (sorted, sections before teachers before
rooms, so two writes can't deadlock) and check inside the same transaction.
"""
import uuid
from dataclasses import dataclass
from datetime import date as Date
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError
from modules.academics.models import CalendarEvent, Room, Section, TeachingAssignment
from modules.academics.selectors import electives_share_students, is_elective
from modules.staff.models import StaffMember

from .models import LessonChange, TimetableEntry

ENTRY_RELATED = (
    "period",
    "room",
    "term",
    "teaching_assignment__section__program",
    "teaching_assignment__section__campus",
    "teaching_assignment__subject",
    "teaching_assignment__teacher",
)


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


# ---------------------------------------------------------------------------
# When a lesson runs: its year, its term, and its own validity
# ---------------------------------------------------------------------------
def span(*, academic_year, term=None, valid_from=None, valid_until=None) -> tuple[Date, Date]:
    """First and last day a weekly lesson runs."""
    starts = [academic_year.start_date, term.start_date if term else None, valid_from]
    ends = [academic_year.end_date, term.end_date if term else None, valid_until]
    return max(d for d in starts if d), min(d for d in ends if d)


def entry_span(entry) -> tuple[Date, Date]:
    return span(academic_year=entry.teaching_assignment.section.academic_year, term=entry.term,
                valid_from=entry.valid_from, valid_until=entry.valid_until)


def running_between(start: Date, end: Date, prefix: str = "") -> Q:
    """Entries whose own dates and term overlap ``start``..``end``. (The
    academic year is compared separately, by id.)"""
    p = prefix
    return (
        (Q(**{f"{p}valid_from__isnull": True}) | Q(**{f"{p}valid_from__lte": end}))
        & (Q(**{f"{p}valid_until__isnull": True}) | Q(**{f"{p}valid_until__gte": start}))
        & (Q(**{f"{p}term__isnull": True})
           | Q(**{f"{p}term__start_date__lte": end, f"{p}term__end_date__gte": start}))
    )


def running_from(day: Date) -> Q:
    """Entries that haven't ended before ``day``: the timetable from then on."""
    return Q(valid_until__isnull=True) | Q(valid_until__gte=day)


# ---------------------------------------------------------------------------
# Locks and the 409
# ---------------------------------------------------------------------------
def lock(*, sections=(), teachers=(), rooms=()) -> None:
    """``SELECT … FOR UPDATE`` on every row a write depends on. Call inside
    ``transaction.atomic``."""
    for model, ids in ((Section, sections), (StaffMember, teachers), (Room, rooms)):
        ids = sorted({pk for pk in ids if pk is not None})
        if ids:
            list(model.objects.select_for_update().filter(pk__in=ids).order_by("pk"))


def _describe(entry, kind, *, on=None) -> dict:
    ta = entry.teaching_assignment
    return {
        "campus_id": ta.section.campus_id,
        "kind": kind,
        "entry": entry.pk,
        "date": on.isoformat() if on else None,
        "section": ta.section.display_name,
        "campus": ta.section.campus.name,
        "subject": ta.subject.name,
        "teacher": ta.teacher.full_name,
        "room": entry.room.name if entry.room else None,
        "period": entry.period.name,
        "start_time": entry.period.start_time.strftime("%H:%M"),
        "end_time": entry.period.end_time.strftime("%H:%M"),
    }


def _redact(clash: dict, visible_campus_ids) -> dict:
    """Keep only when and where for a clash at a campus the caller can't see:
    enough to know the teacher is busy, without that campus's timetable."""
    campus_id = clash.pop("campus_id")
    if visible_campus_ids is None or campus_id in visible_campus_ids:
        return clash
    keep = ("kind", "campus", "date", "start_time", "end_time")
    return {key: (clash[key] if key in keep else None) for key in clash}


def raise_if_clashes(clashes: list[dict], visible_campus_ids=None, prefix="") -> None:
    if not clashes:
        return
    clashes = [_redact(dict(clash), visible_campus_ids) for clash in clashes]
    first = clashes[0]
    what = (f"{first['subject']} for {first['section']}" if first["section"]
            else f"a lesson at {first['campus']}")
    when = f"{first['date']} " if first["date"] else ""
    raise ConflictError(
        f"{prefix}Clashes with {what} ({when}{first['start_time']}–{first['end_time']}): "
        f"same {first['kind']}.",
        code="timetable_clash",
        details={"clashes": clashes},
    )


# ---------------------------------------------------------------------------
# Weekly lessons
# ---------------------------------------------------------------------------
def find_clashes(*, organization_id, assignment, day_of_week, period, room=None, term=None,
                 exclude_pks=(), group=None, valid_from=None, valid_until=None) -> list[dict]:
    """Everything this weekly lesson would collide with, over the dates it runs."""
    section = assignment.section
    start, end = span(academic_year=section.academic_year, term=term,
                      valid_from=valid_from, valid_until=valid_until)
    if start > end:
        return []
    candidates = TimetableEntry.objects.filter(running_between(start, end)).filter(
        organization_id=organization_id,
        day_of_week=day_of_week,
        period__start_time__lt=period.end_time,
        period__end_time__gt=period.start_time,
        teaching_assignment__section__academic_year_id=section.academic_year_id,
    ).filter(
        Q(teaching_assignment__teacher_id=assignment.teacher_id)
        | Q(teaching_assignment__section_id=section.pk)
        | (Q(room_id=room.pk) if room is not None else Q(pk__in=[]))
    ).exclude(pk__in=list(exclude_pks)).select_related(*ENTRY_RELATED)

    clashes = []
    mine_elective = None
    for other in candidates:
        other_ta = other.teaching_assignment
        same_class = group is not None and other.combined_group == group
        together = _taught_together(assignment, other_ta)
        if other_ta.teacher_id == assignment.teacher_id and not same_class:
            clashes.append(_describe(other, "teacher"))
        if room is not None and other.room_id == room.pk and not same_class and together != "co_teaching":
            clashes.append(_describe(other, "room"))
        if other_ta.section_id == section.pk and not together:
            if mine_elective is None:
                mine_elective = is_elective(section, assignment.subject_id)
            parallel = (
                mine_elective
                and is_elective(section, other_ta.subject_id)
                and not electives_share_students(section, assignment.subject_id, other_ta.subject_id)
            )
            if not parallel:
                clashes.append(_describe(other, "section"))

    clashes += _upcoming_substitutions(
        organization_id=organization_id, teacher_id=assignment.teacher_id, room=room,
        day_of_week=day_of_week, period=period, start=max(start, timezone.localdate()), end=end,
        exclude_entries=exclude_pks, group=group,
    )
    return clashes


def _taught_together(a, b) -> str | None:
    """Two assignments of one section and subject that may meet at the same
    time: a co-teacher alongside the teacher (same room), or practical groups
    in parallel (each in its own lab)."""
    if a.section_id != b.section_id or a.subject_id != b.subject_id or a.teacher_id == b.teacher_id:
        return None
    roles = {a.role, b.role}
    if TeachingAssignment.Role.CO_TEACHING in roles:
        return "co_teaching"
    if roles == {TeachingAssignment.Role.PRACTICAL}:
        return "practical_groups"
    return None


def _upcoming_substitutions(*, organization_id, teacher_id, room, day_of_week, period, start, end,
                            exclude_entries=(), group=None) -> list[dict]:
    """Future lesson changes that already put this teacher or room somewhere
    else at this weekday and time, between ``start`` and ``end``."""
    if start > end:
        return []
    changes = LessonChange.objects.filter(
        organization_id=organization_id,
        date__range=(start, end),
        is_cancelled=False,
        entry__day_of_week=day_of_week,
        entry__deleted_at__isnull=True,
        entry__period__start_time__lt=period.end_time,
        entry__period__end_time__gt=period.start_time,
    ).filter(
        Q(substitute_teacher_id=teacher_id)
        | (Q(room_id=room.pk) if room is not None else Q(pk__in=[]))
    ).exclude(entry_id__in=list(exclude_entries)).select_related(
        *(f"entry__{name}" for name in ENTRY_RELATED)
    )
    if group is not None:
        changes = changes.exclude(entry__combined_group=group)
    clashes = []
    for change in changes:
        kind = "teacher" if change.substitute_teacher_id == teacher_id else "room"
        clashes.append(_describe(change.entry, kind, on=change.date))
    return clashes


def lock_and_check(*, organization_id, assignment, day_of_week, period, room=None, term=None,
                   exclude_pks=(), group=None, visible_campus_ids=None,
                   valid_from=None, valid_until=None) -> None:
    """Lock the rows this weekly lesson depends on, then refuse it if it
    would clash. ``visible_campus_ids`` (None: all) limits which clashes are
    described in full; the others only say when and at which campus."""
    lock(sections=[assignment.section_id], teachers=[assignment.teacher_id],
         rooms=[room.pk] if room else [])
    raise_if_clashes(
        find_clashes(
            organization_id=organization_id, assignment=assignment, day_of_week=day_of_week,
            period=period, room=room, term=term, exclude_pks=exclude_pks, group=group,
            valid_from=valid_from, valid_until=valid_until,
        ),
        visible_campus_ids,
    )


# ---------------------------------------------------------------------------
# Lessons on a date
# ---------------------------------------------------------------------------
def entries_on(day: Date, queryset=None, weekdays=None):
    """Weekly entries that could take place on ``day``: its weekday (or
    ``weekdays``, for make-up days), inside the section's academic year, the
    lesson's own validity and, for term lessons, the term. The calendar is
    applied by ``lessons_on``."""
    queryset = TimetableEntry.objects.all() if queryset is None else queryset
    return queryset.filter(
        running_between(day, day),
        day_of_week__in=list(weekdays or [day.isoweekday()]),
        teaching_assignment__section__academic_year__start_date__lte=day,
        teaching_assignment__section__academic_year__end_date__gte=day,
    )


@dataclass
class Lesson:
    """One lesson as it happens on a date, after that day's change and the
    academic calendar."""

    entry: TimetableEntry
    date: Date
    change: LessonChange | None = None
    closed_by: CalendarEvent | None = None

    @property
    def is_cancelled(self) -> bool:
        return self.closed_by is not None or bool(self.change and self.change.is_cancelled)

    @property
    def teacher(self):
        if self.change and self.change.substitute_teacher_id:
            return self.change.substitute_teacher
        return self.entry.teaching_assignment.teacher

    @property
    def room(self):
        if self.change and self.change.room_id:
            return self.change.room
        return self.entry.room


def lessons_on(day: Date, *, organization_id, section=None, teacher=None, room=None,
               entries=None) -> list[Lesson]:
    """The lessons of one day, with that day's changes and calendar applied.

    ``teacher`` and ``room`` follow the changes: a substitute sees the lessons
    they cover and not the ones handed away; a room shows lessons moved into
    it. Cancelled lessons, and lessons on a holiday, closure or exam day
    (``closed_by``), are included with ``is_cancelled`` so a day view can
    show them struck out; they occupy no teacher or room. On a make-up day
    the sections it covers follow another weekday's timetable.
    """
    base = TimetableEntry.objects.filter(organization_id=organization_id) if entries is None else entries
    events = list(CalendarEvent.objects.filter(start_date__lte=day, end_date__gte=day)
                  .filter(**({"organization_id": organization_id} if organization_id else {})))
    weekdays = {day.isoweekday()} | {e.runs_timetable_of for e in events
                                     if e.kind == CalendarEvent.Kind.MAKEUP_DAY}
    todays_changes = LessonChange.objects.filter(date=day)
    q = Q()
    if section is not None:
        q &= Q(teaching_assignment__section_id=getattr(section, "pk", section))
    if teacher is not None:
        teacher_id = getattr(teacher, "pk", teacher)
        q &= Q(teaching_assignment__teacher_id=teacher_id) | Q(
            pk__in=todays_changes.filter(substitute_teacher_id=teacher_id).values("entry_id")
        )
    if room is not None:
        room_id = getattr(room, "pk", room)
        q &= Q(room_id=room_id) | Q(pk__in=todays_changes.filter(room_id=room_id).values("entry_id"))
    candidates = list(entries_on(day, base.filter(q), weekdays).select_related(*ENTRY_RELATED)
                      .order_by("period__start_time", "pk"))

    entries, closed = [], {}
    for entry in candidates:
        section_ = entry.teaching_assignment.section
        applicable = [e for e in events
                      if e.organization_id == entry.organization_id and e.applies_to(section_)]
        makeup = next((e for e in applicable if e.kind == CalendarEvent.Kind.MAKEUP_DAY), None)
        if entry.day_of_week != (makeup.runs_timetable_of if makeup else day.isoweekday()):
            continue
        entries.append(entry)
        closed[entry.pk] = next((e for e in applicable if e.suspends_classes), None)

    changes = {
        change.entry_id: change
        for change in todays_changes.filter(entry__in=[e.pk for e in entries])
        .select_related("substitute_teacher", "room")
    }
    lessons = [Lesson(entry, day, changes.get(entry.pk), closed[entry.pk]) for entry in entries]
    if teacher is not None:
        lessons = [x for x in lessons if x.teacher.pk == teacher_id]
    if room is not None:
        lessons = [x for x in lessons if x.room is not None and x.room.pk == room_id]
    return lessons


def find_date_clashes(*, organization_id, entry, day, teacher=None, room=None,
                      exclude_change=None) -> list[dict]:
    """What the substitute ``teacher`` or ``room`` is already doing on ``day``
    at the time of ``entry``, other than this lesson's own combined class."""
    period = entry.period
    clashes = []

    def busy(lesson):
        if lesson.is_cancelled or lesson.entry.pk == entry.pk:
            return False
        if entry.combined_group is not None and lesson.entry.combined_group == entry.combined_group:
            return False
        if exclude_change is not None and lesson.change is not None and lesson.change.pk == exclude_change:
            return False
        other = lesson.entry.period
        return _overlaps(period.start_time, period.end_time, other.start_time, other.end_time)

    if teacher is not None:
        for lesson in lessons_on(day, organization_id=organization_id, teacher=teacher):
            if busy(lesson):
                clashes.append(_describe(lesson.entry, "teacher", on=day))
    if room is not None:
        for lesson in lessons_on(day, organization_id=organization_id, room=room):
            if busy(lesson):
                clashes.append(_describe(lesson.entry, "room", on=day))
    return clashes


# ---------------------------------------------------------------------------
# Handing lessons over to another teacher
# ---------------------------------------------------------------------------
def has_run_before(entry, day: Date) -> bool:
    """Did the lesson take place (or could it have) before ``day``?"""
    first, _ = entry_span(entry)
    return first < day


def supersede(entry, *, effective_from: Date, changes: dict, by=None) -> TimetableEntry:
    """Change a lesson from ``effective_from`` on without rewriting its past:
    the entry ends the day before, and a copy with ``changes`` starts that day.

    Lesson changes (substitutes etc.) from that day on follow the lesson if
    it still meets on their date; otherwise the caller must remove them first.
    Call inside ``transaction.atomic``, after checking clashes.
    """
    new = TimetableEntry(
        organization_id=entry.organization_id, teaching_assignment=entry.teaching_assignment,
        day_of_week=entry.day_of_week, period=entry.period, room=entry.room, term=entry.term,
        combined_group=entry.combined_group, valid_from=effective_from, valid_until=entry.valid_until,
    )
    for field, value in changes.items():
        setattr(new, field, value)
    later = LessonChange.objects.filter(entry=entry, date__gte=effective_from)
    if new.day_of_week != entry.day_of_week and later.exists():
        dates = ", ".join(str(d) for d in later.values_list("date", flat=True))
        raise ConflictError(
            f"The lesson has substitutes or changes planned on {dates}, which it would no longer "
            "meet on. Remove those first.",
            code="planned_changes",
        )
    entry.valid_until = effective_from - timedelta(days=1)
    entry.save(update_fields=["valid_until", "updated_at"])
    new.save()
    later.update(entry=new)
    log(AuditLog.Action.UPDATE, instance=entry, module="timetable", actor=by,
        changes={"valid_until": {"before": None, "after": str(entry.valid_until)}},
        metadata={"operation": "superseded", "by_entry": new.pk})
    log(AuditLog.Action.CREATE, instance=new, module="timetable", actor=by,
        metadata={"operation": "supersedes", "entry": entry.pk,
                  "changes": {k: str(getattr(v, "pk", v)) if v is not None else None
                              for k, v in changes.items()}})
    return new


def hand_over(*, assignments, teacher, on: Date | None = None, visible_campus_ids=None,
              by=None) -> list[TeachingAssignment]:
    """Give the lessons of ``assignments`` to ``teacher`` from ``on`` (default
    today).

    For each assignment, the new teacher gets an assignment for the same
    section, subject and role (created if needed). Lessons that already ran
    are ended the day before and continued under the new assignment, so past
    lessons still show who really taught them; lessons that haven't started
    simply move. Old assignments with nothing left from ``on`` are marked
    inactive: a record of who taught before.

    All or nothing: every clash is reported, and nothing moves if any.
    Call inside ``transaction.atomic``.
    """
    on = on or timezone.localdate()
    ids = {a.pk for a in assignments}
    entries = list(
        TimetableEntry.objects.filter(teaching_assignment_id__in=ids).filter(running_from(on))
        .select_related(*ENTRY_RELATED, "teaching_assignment__section__academic_year")
    )
    groups = {e.combined_group for e in entries if e.combined_group}
    outside = (TimetableEntry.objects.filter(combined_group__in=groups).filter(running_from(on))
               .exclude(teaching_assignment_id__in=ids))
    if outside.exists():
        raise ConflictError(
            "Some lessons are combined classes with sections not in this handover. "
            "Include their assignments too, or take those sections out of the combined class.",
            code="combined_class",
        )

    lock(sections=[a.section_id for a in assignments], teachers=[teacher.pk],
         rooms=[e.room_id for e in entries])
    replacements, clashes = {}, []
    for old in assignments:
        new, _ = TeachingAssignment.objects.get_or_create(
            section_id=old.section_id, subject_id=old.subject_id, teacher=teacher, role=old.role,
            defaults={"organization_id": old.organization_id, "periods_per_week": old.periods_per_week},
        )
        if not new.is_active:
            new.is_active = True
            new.save(update_fields=["is_active", "updated_at"])
        replacements[old.pk] = new

    def from_(entry):
        return max(on, entry.valid_from) if entry.valid_from else on

    moving = [e.pk for e in entries]
    for entry in entries:
        clashes += find_clashes(
            organization_id=entry.organization_id, assignment=replacements[entry.teaching_assignment_id],
            day_of_week=entry.day_of_week, period=entry.period, room=entry.room, term=entry.term,
            exclude_pks=moving, group=entry.combined_group,
            valid_from=from_(entry), valid_until=entry.valid_until,
        )
    # Lessons that had different teachers may meet at the same time; they
    # can't all go to one person.
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            same_class = a.combined_group is not None and a.combined_group == b.combined_group
            a_first, a_last = entry_span(a)
            b_first, b_last = entry_span(b)
            dates_meet = max(a_first, b_first, on) <= min(a_last, b_last)
            same_year = (a.teaching_assignment.section.academic_year_id
                         == b.teaching_assignment.section.academic_year_id)
            if (same_year and dates_meet and a.day_of_week == b.day_of_week and not same_class
                    and _overlaps(a.period.start_time, a.period.end_time,
                                  b.period.start_time, b.period.end_time)):
                clashes.append(_describe(b, "teacher"))
    raise_if_clashes(clashes, visible_campus_ids)

    new_groups = {}
    for entry in entries:
        old_ta = entry.teaching_assignment_id
        if has_run_before(entry, on):
            group = entry.combined_group and new_groups.setdefault(entry.combined_group, uuid.uuid4())
            supersede(entry, effective_from=on, by=by,
                      changes={"teaching_assignment": replacements[old_ta], "combined_group": group or None})
        else:
            entry.teaching_assignment = replacements[old_ta]
            entry.save(update_fields=["teaching_assignment", "updated_at"])
            log(AuditLog.Action.UPDATE, instance=entry, module="timetable", actor=by,
                changes={"teaching_assignment": {"before": old_ta, "after": entry.teaching_assignment_id}},
                metadata={"operation": "hand_over"})
    for old in assignments:
        if not old.timetable_entries.filter(running_from(on)).exists():
            old.is_active = False
            old.save(update_fields=["is_active", "updated_at"])
    return list(replacements.values())


# ---------------------------------------------------------------------------
# New bell times from a date (winter timings)
# ---------------------------------------------------------------------------
def retime_schedule(*, schedule, effective_from: Date, new_times: dict, visible_campus_ids=None,
                    by=None) -> list:
    """Give some periods of ``schedule`` new clock times from
    ``effective_from``, and move their lessons along.

    ``new_times`` maps a period to ``(start_time, end_time)``. A period that
    has been in use ends the day before and a new one (same name) starts;
    lessons that already ran are ended and continued in it, so the old times
    stay true for past dates. Every moved lesson is checked against lessons
    that aren't moving (another shift's, a shared teacher's). All or nothing.
    Call inside ``transaction.atomic``.
    """
    from .models import Period

    periods = {p.pk: p for p in new_times}
    valid = Period.objects.filter(schedule=schedule).filter(
        Q(valid_from__isnull=True) | Q(valid_from__lte=effective_from),
        Q(valid_until__isnull=True) | Q(valid_until__gte=effective_from),
    )
    # The day as it will look: moved periods at their new times, the rest as they are.
    day = [(p, *new_times.get(p, (p.start_time, p.end_time))) for p in valid]
    for i, (a, a_start, a_end) in enumerate(day):
        if a_end <= a_start:
            raise ConflictError(f"{a.name}: the end must be after the start.", code="invalid_times")
        for b, b_start, b_end in day[i + 1:]:
            if _overlaps(a_start, a_end, b_start, b_end):
                raise ConflictError(f"{a.name} and {b.name} would overlap.", code="invalid_times")

    entries = list(
        TimetableEntry.objects.filter(period_id__in=periods).filter(running_from(effective_from))
        .select_related(*ENTRY_RELATED, "teaching_assignment__section__academic_year")
    )
    lock(sections=[e.teaching_assignment.section_id for e in entries],
         teachers=[e.teaching_assignment.teacher_id for e in entries],
         rooms=[e.room_id for e in entries])
    moving = [e.pk for e in entries]
    clashes = []
    for entry in entries:
        start, end = new_times[entry.period]
        clashes += find_clashes(
            organization_id=entry.organization_id, assignment=entry.teaching_assignment,
            day_of_week=entry.day_of_week, period=Period(start_time=start, end_time=end),
            room=entry.room, term=entry.term, exclude_pks=moving, group=entry.combined_group,
            valid_from=max(effective_from, entry.valid_from or effective_from),
            valid_until=entry.valid_until,
        )
    raise_if_clashes(clashes, visible_campus_ids, prefix="New bell times: ")

    replacement = {}
    for period, (start, end) in new_times.items():
        # The old times stay true until the change even for a period no lesson
        # uses yet: lessons added later must get them. Only a period that
        # doesn't start before the change is simply edited.
        starts_before = period.valid_from is None or period.valid_from < effective_from
        if not starts_before:
            period.start_time, period.end_time = start, end
            period.save(update_fields=["start_time", "end_time", "updated_at"])
            replacement[period.pk] = period
            continue
        new = Period.objects.create(
            organization_id=period.organization_id, schedule=schedule, name=period.name,
            start_time=start, end_time=end, is_break=period.is_break,
            valid_from=effective_from, valid_until=period.valid_until,
        )
        period.valid_until = effective_from - timedelta(days=1)
        period.save(update_fields=["valid_until", "updated_at"])
        replacement[period.pk] = new
        log(AuditLog.Action.UPDATE, instance=period, module="timetable", actor=by,
            metadata={"operation": "retimed", "from": str(effective_from), "by_period": new.pk})

    new_groups = {}
    for entry in entries:
        new_period = replacement[entry.period_id]
        if new_period.pk == entry.period_id:
            continue  # retimed in place
        if has_run_before(entry, effective_from):
            group = entry.combined_group and new_groups.setdefault(entry.combined_group, uuid.uuid4())
            supersede(entry, effective_from=effective_from, by=by,
                      changes={"period": new_period, "combined_group": group or None})
        else:
            entry.period = new_period
            entry.save(update_fields=["period", "updated_at"])
    return list(replacement.values())


def continue_across_retimes(entry, *, by=None, visible_campus_ids=None) -> TimetableEntry:
    """A lesson in a period whose bell times change from a date carries on in
    the new version of that period, just as ``retime_schedule`` moves the
    lessons that existed when it ran. Needed for lessons added (by hand or by
    the generator) after new times were scheduled. Each continuation is
    clash-checked; if the period simply ends, the lesson ends with it.
    Call inside ``transaction.atomic``, after saving ``entry``. Returns the
    last version.
    """
    from .models import Period

    while entry.period.valid_until is not None:
        period = entry.period
        _, last = entry_span(entry)
        if last <= period.valid_until:
            break
        successor = Period.objects.filter(
            schedule_id=period.schedule_id, name=period.name,
            valid_from=period.valid_until + timedelta(days=1),
        ).first()
        if successor is None:
            entry.valid_until = period.valid_until
            entry.save(update_fields=["valid_until", "updated_at"])
            break
        raise_if_clashes(
            find_clashes(
                organization_id=entry.organization_id, assignment=entry.teaching_assignment,
                day_of_week=entry.day_of_week, period=successor, room=entry.room, term=entry.term,
                exclude_pks=[entry.pk], group=entry.combined_group,
                valid_from=successor.valid_from, valid_until=entry.valid_until,
            ),
            visible_campus_ids, prefix=f"From {successor.valid_from} (new bell times): ",
        )
        entry = supersede(entry, effective_from=successor.valid_from, changes={"period": successor}, by=by)
    return entry
