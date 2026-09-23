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
from dataclasses import dataclass
from datetime import date as Date

from django.db.models import Q
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError
from modules.academics.models import Room, Section, TeachingAssignment
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
                 exclude_pks=(), group=None) -> list[dict]:
    """Everything this weekly lesson would collide with."""
    section = assignment.section
    candidates = TimetableEntry.objects.filter(
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
    if term is not None:
        candidates = candidates.filter(Q(term__isnull=True) | Q(term=term))

    clashes = []
    mine_elective = None
    for other in candidates:
        other_ta = other.teaching_assignment
        same_class = group is not None and other.combined_group == group
        if other_ta.teacher_id == assignment.teacher_id and not same_class:
            clashes.append(_describe(other, "teacher"))
        if room is not None and other.room_id == room.pk and not same_class:
            clashes.append(_describe(other, "room"))
        if other_ta.section_id == section.pk:
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
        day_of_week=day_of_week, period=period, term=term, academic_year=section.academic_year,
        exclude_entries=exclude_pks, group=group,
    )
    return clashes


def _upcoming_substitutions(*, organization_id, teacher_id, room, day_of_week, period, term,
                            academic_year, exclude_entries=(), group=None) -> list[dict]:
    """Future lesson changes that already put this teacher or room somewhere
    else at this weekday and time."""
    start = max(timezone.localdate(), term.start_date if term else academic_year.start_date)
    end = term.end_date if term else academic_year.end_date
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
                   exclude_pks=(), group=None, visible_campus_ids=None) -> None:
    """Lock the rows this weekly lesson depends on, then refuse it if it
    would clash. ``visible_campus_ids`` (None: all) limits which clashes are
    described in full; the others only say when and at which campus."""
    lock(sections=[assignment.section_id], teachers=[assignment.teacher_id],
         rooms=[room.pk] if room else [])
    raise_if_clashes(
        find_clashes(
            organization_id=organization_id, assignment=assignment, day_of_week=day_of_week,
            period=period, room=room, term=term, exclude_pks=exclude_pks, group=group,
        ),
        visible_campus_ids,
    )


# ---------------------------------------------------------------------------
# Lessons on a date
# ---------------------------------------------------------------------------
def entries_on(day: Date, queryset=None):
    """Weekly entries that take place on ``day``: its weekday, inside the
    section's academic year and, for term lessons, inside the term."""
    queryset = TimetableEntry.objects.all() if queryset is None else queryset
    return queryset.filter(
        day_of_week=day.isoweekday(),
        teaching_assignment__section__academic_year__start_date__lte=day,
        teaching_assignment__section__academic_year__end_date__gte=day,
    ).filter(Q(term__isnull=True) | Q(term__start_date__lte=day, term__end_date__gte=day))


@dataclass
class Lesson:
    """One lesson as it happens on a date, after that day's change."""

    entry: TimetableEntry
    date: Date
    change: LessonChange | None = None

    @property
    def is_cancelled(self) -> bool:
        return bool(self.change and self.change.is_cancelled)

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
    """The lessons of one day, with that day's changes applied.

    ``teacher`` and ``room`` follow the changes: a substitute sees the lessons
    they cover and not the ones handed away; a room shows lessons moved into
    it. Cancelled lessons are included (``is_cancelled``) so a day view can
    show them struck out; they occupy no teacher or room.
    """
    base = TimetableEntry.objects.filter(organization_id=organization_id) if entries is None else entries
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
    entries = list(entries_on(day, base.filter(q)).select_related(*ENTRY_RELATED)
                   .order_by("period__start_time", "pk"))
    changes = {
        change.entry_id: change
        for change in todays_changes.filter(entry__in=[e.pk for e in entries])
        .select_related("substitute_teacher", "room")
    }
    lessons = [Lesson(entry, day, changes.get(entry.pk)) for entry in entries]
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
def hand_over(*, assignments, teacher, visible_campus_ids=None, by=None) -> list[TeachingAssignment]:
    """Give every weekly lesson of ``assignments`` to ``teacher``.

    For each assignment, the new teacher gets an assignment for the same
    section and subject (created if needed), and its lessons move over. The
    old assignments stay, without lessons, as a record of who taught before.
    All or nothing: every clash is reported, and nothing moves if any.
    Call inside ``transaction.atomic``.
    """
    ids = {a.pk for a in assignments}
    entries = list(
        TimetableEntry.objects.filter(teaching_assignment_id__in=ids).select_related(*ENTRY_RELATED)
    )
    groups = {e.combined_group for e in entries if e.combined_group}
    outside = TimetableEntry.objects.filter(combined_group__in=groups).exclude(teaching_assignment_id__in=ids)
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
            section_id=old.section_id, subject_id=old.subject_id, teacher=teacher,
            defaults={"organization_id": old.organization_id, "periods_per_week": old.periods_per_week},
        )
        replacements[old.pk] = new
    moving = [e.pk for e in entries]
    for entry in entries:
        new = replacements[entry.teaching_assignment_id]
        clashes += find_clashes(
            organization_id=entry.organization_id, assignment=new, day_of_week=entry.day_of_week,
            period=entry.period, room=entry.room, term=entry.term,
            exclude_pks=moving, group=entry.combined_group,
        )
    # Lessons that had different teachers may meet at the same time; they
    # can't all go to one person.
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            same_class = a.combined_group is not None and a.combined_group == b.combined_group
            same_year = (a.teaching_assignment.section.academic_year_id
                         == b.teaching_assignment.section.academic_year_id)
            if (same_year and a.day_of_week == b.day_of_week and not same_class
                    and (a.term_id is None or b.term_id is None or a.term_id == b.term_id)
                    and _overlaps(a.period.start_time, a.period.end_time,
                                  b.period.start_time, b.period.end_time)):
                clashes.append(_describe(b, "teacher"))
    raise_if_clashes(clashes, visible_campus_ids)
    for entry in entries:
        before = entry.teaching_assignment_id
        entry.teaching_assignment = replacements[before]
        entry.save(update_fields=["teaching_assignment", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=entry, module="timetable", actor=by,
            changes={"teaching_assignment": {"before": before, "after": entry.teaching_assignment_id}},
            metadata={"operation": "hand_over"})
    return list(replacements.values())
