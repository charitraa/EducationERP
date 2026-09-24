"""Fill the weekly timetable automatically.

Input: some sections of one campus, a bell schedule, the teaching days, and
each teaching assignment's ``periods_per_week``. Lessons already on the
timetable count towards that number, so generating again only fills gaps
and a hand-made timetable can be finished off automatically.

Greedy, in rounds: each round gives every assignment that still needs
lessons one more, so no subject is left for last. Each lesson takes the free
slot with the best score:

1. fewest lessons of this assignment already that day (spread over the week);
2. for an elective, a slot where other electives of the section already run
   (electives go in parallel, so students split up for them);
3. the lightest day for the section;
4. the earliest period, then the earliest day.

A slot is free when the teacher (at any campus), the room and the section
are free, using the same rules as clash detection. Rooms: the section's home
room; a parallel elective takes the first free room of the campus instead.

Deterministic: the same input gives the same timetable. What can't be
placed is reported, never forced.
"""
from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Q

from modules.academics.models import CurriculumSubject, Room, TeachingAssignment
from modules.academics.selectors import electives_share_students

from .models import TimetableEntry
from .services import running_between, span


@dataclass
class Busy:
    day: int
    start: object
    end: object
    subject_id: int | None = None
    elective: bool = False

    def overlaps(self, day, start, end) -> bool:
        return self.day == day and self.start < end and start < self.end


@dataclass
class Proposal:
    assignment: TeachingAssignment
    day: int
    period: object
    room: Room | None


@dataclass
class Plan:
    proposals: list[Proposal] = field(default_factory=list)
    unplaced: list[dict] = field(default_factory=list)
    start: object = None


def plan_timetable(*, sections, schedule, days, term=None, start=None) -> Plan:
    """``start`` (default today, or the year/term start if later) is when the
    new lessons begin; only lessons running from then on are in the way."""
    from django.utils import timezone

    today = timezone.localdate()
    section_ids = [s.pk for s in sections]
    year_ids = {s.academic_year_id for s in sections}
    first, last = span(academic_year=sections[0].academic_year, term=term)
    start = max(start or today, first)
    periods = list(
        schedule.periods.filter(is_break=False)
        .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=start))
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=start))
        .order_by("start_time")
    )

    assignments = list(
        TeachingAssignment.objects.filter(section_id__in=section_ids, periods_per_week__isnull=False,
                                          is_active=True)
        .select_related("section__program", "section__home_room", "subject", "teacher")
        .order_by("section_id", "subject__name", "pk")
    )
    electives = {
        (c.program_id, c.level, c.subject_id)
        for c in CurriculumSubject.objects.filter(
            program_id__in={s.program_id for s in sections}, is_elective=True
        )
    }

    def elective(assignment, subject_id=None):
        section = assignment.section
        return (section.program_id, section.level, subject_id or assignment.subject_id) in electives

    # What already happens in the week, from every campus for teachers.
    existing = TimetableEntry.objects.filter(
        running_between(start, last),
        teaching_assignment__section__academic_year_id__in=year_ids,
    ).filter(
        Q(teaching_assignment__teacher_id__in={a.teacher_id for a in assignments})
        | Q(teaching_assignment__section_id__in=section_ids)
        | Q(room__campus_id=schedule.campus_id)
    ).select_related("period", "teaching_assignment__section")

    teacher_busy, room_busy, section_busy = defaultdict(list), defaultdict(list), defaultdict(list)
    have = defaultdict(int)
    per_day = defaultdict(int)  # (assignment, day) -> lessons
    for entry in existing:
        ta = entry.teaching_assignment
        slot = (entry.day_of_week, entry.period.start_time, entry.period.end_time)
        teacher_busy[ta.teacher_id].append(Busy(*slot))
        if entry.room_id:
            room_busy[entry.room_id].append(Busy(*slot))
        section_busy[ta.section_id].append(Busy(*slot, subject_id=ta.subject_id, elective=elective(ta)))
        have[ta.pk] += 1
        per_day[(ta.pk, entry.day_of_week)] += 1

    rooms = list(Room.objects.filter(campus_id=schedule.campus_id, is_active=True).order_by("code", "pk"))
    shares_cache = {}

    def shares(section, a, b):
        key = (section.pk, min(a, b), max(a, b))
        if key not in shares_cache:
            shares_cache[key] = electives_share_students(section, a, b)
        return shares_cache[key]

    def free(busy_list, day, period):
        return not any(b.overlaps(day, period.start_time, period.end_time) for b in busy_list)

    def section_fit(assignment, day, period):
        """None if the section can't take the lesson here; else whether it
        would run alongside other electives."""
        section = assignment.section
        here = [b for b in section_busy[section.pk] if b.overlaps(day, period.start_time, period.end_time)]
        if not here:
            return False
        if not elective(assignment):
            return None
        for b in here:
            if not b.elective or shares(section, assignment.subject_id, b.subject_id):
                return None
        return True

    def pick_room(assignment, day, period, parallel):
        """(room, usable). No home room: the lesson isn't tied to a room."""
        home = assignment.section.home_room
        if home is None:
            return None, True
        if free(room_busy[home.pk], day, period):
            return home, True
        if parallel:
            for room in rooms:
                if free(room_busy[room.pk], day, period):
                    return room, True
        return None, False

    needed = {a.pk: max(0, a.periods_per_week - have[a.pk]) for a in assignments}
    order = sorted(assignments, key=lambda a: (-needed[a.pk], a.section_id, a.subject.name, a.pk))
    plan = Plan(start=start)
    stuck = set()

    while any(needed[a.pk] and a.pk not in stuck for a in order):
        for assignment in order:
            if not needed[assignment.pk] or assignment.pk in stuck:
                continue
            section = assignment.section
            best = None
            for day_index, day in enumerate(days):
                section_load = sum(1 for b in section_busy[section.pk] if b.day == day)
                for period_index, period in enumerate(periods):
                    if not free(teacher_busy[assignment.teacher_id], day, period):
                        continue
                    parallel = section_fit(assignment, day, period)
                    if parallel is None:
                        continue
                    room, ok = pick_room(assignment, day, period, parallel)
                    if not ok:
                        continue
                    score = (per_day[(assignment.pk, day)], 0 if parallel else 1,
                             section_load, period_index, day_index)
                    if best is None or score < best[0]:
                        best = (score, day, period, room)
            if best is None:
                stuck.add(assignment.pk)
                continue
            _, day, period, room = best
            plan.proposals.append(Proposal(assignment, day, period, room))
            slot = (day, period.start_time, period.end_time)
            teacher_busy[assignment.teacher_id].append(Busy(*slot))
            if room is not None:
                room_busy[room.pk].append(Busy(*slot))
            section_busy[section.pk].append(
                Busy(*slot, subject_id=assignment.subject_id, elective=elective(assignment))
            )
            per_day[(assignment.pk, day)] += 1
            needed[assignment.pk] -= 1

    for assignment in assignments:
        if needed[assignment.pk]:
            plan.unplaced.append({
                "teaching_assignment": assignment.pk,
                "section": assignment.section.display_name,
                "subject": assignment.subject.name,
                "teacher": assignment.teacher.full_name,
                "missing": needed[assignment.pk],
                "reason": "No slot left where the teacher, the section and a room are all free.",
            })
    return plan
