"""Exam rules: setting up and scheduling, seating, admit cards, and marks.

Views and serializers call these so a rule lives in one place. Results are
computed in ``results.py``.
"""
from dataclasses import dataclass
from datetime import date as Date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.permissions.selectors import campus_ids_with_permission
from modules.academics.models import CalendarEvent, CurriculumSubject, Section, TeachingAssignment
from modules.academics.selectors import electives_share_students, is_elective, students_taking
from modules.attendance.services import org_today
from modules.staff.selectors import staff_member_for_user
from modules.students.models import Enrollment

from .models import (
    AdmitCard,
    Exam,
    ExamComponent,
    ExamSubject,
    GradeBand,
    GradeScale,
    DivisionBand,
    Invigilation,
    Mark,
    MarkCorrection,
    MarkSheet,
    MarkStatus,
    SeatAllocation,
)
from . import grading

MODULE = "examinations"
VIEW, MANAGE, MARK, PUBLISH = "exams.view", "exams.manage", "exams.mark", "exams.publish"


def holds(user, code: str, campus_id) -> bool:
    """Does ``user`` hold ``code`` at that campus (or organization-wide)?"""
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


# ---------------------------------------------------------------------------
# Grade scales
# ---------------------------------------------------------------------------
PRESETS = {
    # A common letter-and-GPA table (pass at 35%). A starting point: a board's
    # circular can change, so check it against the current one.
    "neb-style": {
        "bands": [
            (90, "A+", "4.0", "Outstanding", True), (80, "A", "3.6", "Excellent", True),
            (70, "B+", "3.2", "Very good", True), (60, "B", "2.8", "Good", True),
            (50, "C+", "2.4", "Satisfactory", True), (40, "C", "2.0", "Acceptable", True),
            (35, "D", "1.6", "Basic", True), (0, "NG", "0", "Not graded", False),
        ],
        "divisions": [],
    },
    # Marks and percentage only, classed into divisions.
    "percentage": {
        "bands": [(35, "Pass", "0", "Pass", True), (0, "Fail", "0", "Fail", False)],
        "divisions": [(80, "Distinction"), (60, "First division"), (45, "Second division"),
                      (35, "Third division")],
    },
}


def preset_bands(name: str) -> tuple[list[dict], list[dict]]:
    preset = PRESETS[name]
    bands = [{"min_percentage": Decimal(m), "letter": letter, "grade_point": Decimal(gp), "remark": remark,
              "is_pass": ok} for m, letter, gp, remark, ok in preset["bands"]]
    divisions = [{"min_percentage": Decimal(m), "name": n} for m, n in preset["divisions"]]
    return bands, divisions


def scale_in_use(scale: GradeScale) -> bool:
    """Published results depend on the bands, so they can't change under them."""
    return (scale.exams.filter(status=Exam.Status.PUBLISHED).exists()
            or scale.result_plans.filter(status="published").exists())


def replace_bands(scale: GradeScale, bands: list[dict], divisions: list[dict]) -> None:
    """Swap in a scale's bands and divisions in one go, checking they're usable."""
    if scale_in_use(scale):
        raise ConflictError("Published results use this grade scale, so its bands can't change. "
                            "Make a new scale for later exams.", code="scale_in_use")
    problems = grading.validate_bands(bands, divisions)
    if problems:
        raise ServiceError(" ".join(problems), code="scale_invalid", details={"problems": problems})
    with transaction.atomic():
        scale.bands.all().delete()
        scale.divisions.all().delete()
        GradeBand.objects.bulk_create([GradeBand(scale=scale, **b) for b in bands])
        DivisionBand.objects.bulk_create([DivisionBand(scale=scale, **d) for d in divisions])


def resolve_scale(organization_id: int, program) -> GradeScale:
    """The program's own scale, else the organization's default."""
    scales = GradeScale.objects.filter(organization_id=organization_id)
    scale = scales.filter(program=program).first() or scales.filter(program__isnull=True).first()
    if scale is None:
        raise ServiceError(
            f"{program.name} has no grade scale, and the organization has no default. "
            "Create one under /grades/scales/ first.", code="no_grade_scale")
    if not scale.bands.filter(min_percentage=0).exists():
        raise ServiceError(f"The grade scale '{scale.name}' has no bands yet.", code="scale_incomplete")
    return scale


# ---------------------------------------------------------------------------
# Exam structure
# ---------------------------------------------------------------------------
def sections_for(exam: Exam, level: int | None = None):
    """The classes that sit this exam."""
    sections = Section.objects.filter(campus_id=exam.campus_id, program_id=exam.program_id,
                                      academic_year_id=exam.academic_year_id)
    return sections.filter(level=level) if level is not None else sections


def exam_levels(exam: Exam) -> list[int]:
    return sorted(set(exam.subjects.values_list("level", flat=True)))


def paper_day(paper: ExamSubject, exam: Exam | None = None) -> Date:
    """The date whose enrollments and elective choices decide who sits a paper."""
    exam = exam or paper.exam
    return paper.date or exam.start_date or timezone.localdate()


def ensure_open_for_structure(exam: Exam) -> None:
    if exam.status == Exam.Status.PUBLISHED:
        raise ConflictError("The results are published, so the exam can't be changed. "
                            "Unpublish it first.", code="exam_published")


def ensure_no_marks(paper: ExamSubject) -> None:
    if Mark.objects.filter(sheet__exam_subject=paper).exists():
        raise ConflictError("Marks are already entered for this paper, so its marks structure "
                            "can't change.", code="marks_entered")


def add_curriculum(exam: Exam, levels: list[int], *, full_marks: Decimal, pass_marks: Decimal,
                   kind: str = ExamComponent.Kind.THEORY) -> list[ExamSubject]:
    """A paper for every subject the program teaches at each level, each with
    one component, ready for dates and marks tweaks."""
    ensure_open_for_structure(exam)
    program = exam.program
    for level in levels:
        if not program.has_level(level):
            raise ServiceError(f"{program.name} has no {program.level_label(level)}.", code="bad_level")
    created = []
    with transaction.atomic():
        for entry in CurriculumSubject.objects.filter(program=program, level__in=levels).select_related("subject"):
            paper, is_new = ExamSubject.objects.get_or_create(
                exam=exam, subject=entry.subject, level=entry.level,
                defaults={"organization_id": exam.organization_id})
            if is_new:
                ExamComponent.objects.create(
                    organization_id=exam.organization_id, exam_subject=paper, kind=kind,
                    name=ExamComponent.Kind(kind).label, full_marks=full_marks, pass_marks=pass_marks)
                created.append(paper)
    return created


def _overlap(a: ExamSubject, b: ExamSubject) -> bool:
    return (a.date == b.date and None not in (a.start_time, a.end_time, b.start_time, b.end_time)
            and a.start_time < b.end_time and b.start_time < a.end_time)


def check_paper(paper: ExamSubject) -> None:
    """A paper's date and time against the calendar and the exam's other
    papers. Raises with what's wrong; called on every change and on schedule."""
    exam = paper.exam
    if paper.date is not None:
        year = exam.academic_year
        if not year.start_date <= paper.date <= year.end_date:
            raise ServiceError("The date is outside the exam's academic year.", code="outside_year")
        closed = CalendarEvent.objects.filter(
            organization_id=exam.organization_id, start_date__lte=paper.date, end_date__gte=paper.date,
            kind__in=[CalendarEvent.Kind.HOLIDAY, CalendarEvent.Kind.CLOSURE],
        ).filter(Q(campus__isnull=True) | Q(campus_id=exam.campus_id)).filter(
            Q(program__isnull=True) | Q(program_id=exam.program_id))
        event = next((e for e in closed if e.level in (None, paper.level)), None)
        if event is not None:
            raise ServiceError(f"{paper.date} is a {event.get_kind_display().lower()}: {event.title}.",
                               code="on_holiday")
    for other in exam.subjects.filter(level=paper.level, date=paper.date).exclude(pk=paper.pk).select_related("subject"):
        if not _overlap(paper, other):
            continue
        for section in sections_for(exam, paper.level):
            both_elective = (is_elective(section, paper.subject_id) and is_elective(section, other.subject_id))
            if not both_elective or electives_share_students(section, paper.subject_id, other.subject_id):
                raise ConflictError(
                    f"{paper.subject.name} clashes with {other.subject.name}: the same students would "
                    "sit both at the same time.", code="paper_clash",
                    details={"paper": other.pk, "subject": other.subject.name})


def sync_calendar(exam: Exam) -> None:
    """Exam days are days without lessons: keep one calendar event for the
    exam so the timetable and attendance leave those days alone."""
    levels = exam_levels(exam)
    fields = dict(
        kind=CalendarEvent.Kind.EXAM, title=exam.name, start_date=exam.start_date, end_date=exam.end_date,
        campus_id=exam.campus_id, program_id=exam.program_id, level=levels[0] if len(levels) == 1 else None,
        suspends_classes=True,
    )
    if exam.calendar_event_id:
        CalendarEvent.objects.filter(pk=exam.calendar_event_id).update(**fields)
        return
    event = CalendarEvent.objects.create(organization_id=exam.organization_id, **fields)
    exam.calendar_event = event
    exam.save(update_fields=["calendar_event", "updated_at"])


def drop_calendar(exam: Exam) -> None:
    if exam.calendar_event_id:
        event = exam.calendar_event
        exam.calendar_event = None
        exam.save(update_fields=["calendar_event", "updated_at"])
        event.delete()


def schedule(exam: Exam, *, by=None) -> Exam:
    """Draft → scheduled: every paper has a date, time and marks, and none
    clash. From here marks can be entered once a paper has been sat."""
    if exam.status != Exam.Status.DRAFT:
        raise ConflictError("Only an exam being set up can be scheduled.", code="not_draft")
    papers = list(exam.subjects.select_related("subject", "exam__academic_year").prefetch_related("components"))
    if not papers:
        raise ServiceError("Add the exam's papers first.", code="no_papers")
    problems = []
    for paper in papers:
        if paper.date is None or paper.start_time is None or paper.end_time is None:
            problems.append(f"{paper.subject.name} (level {paper.level}) has no date and time.")
        if not paper.components.all():
            problems.append(f"{paper.subject.name} (level {paper.level}) has no marks components.")
    if problems:
        raise ServiceError("The exam isn't ready to schedule.", code="incomplete", details={"problems": problems})
    for paper in papers:
        check_paper(paper)
    with transaction.atomic():
        exam.start_date = min(p.date for p in papers)
        exam.end_date = max(p.date for p in papers)
        exam.status = Exam.Status.SCHEDULED
        exam.save(update_fields=["start_date", "end_date", "status", "updated_at"])
        sync_calendar(exam)
        log(AuditLog.Action.UPDATE, instance=exam, module=MODULE, actor=by,
            changes={"status": {"before": "draft", "after": "scheduled"}})
    return exam


def unschedule(exam: Exam, *, by=None) -> Exam:
    if exam.status != Exam.Status.SCHEDULED:
        raise ConflictError("Only a scheduled exam can go back to being set up.", code="not_scheduled")
    if Mark.objects.filter(sheet__exam_subject__exam=exam).exists():
        raise ConflictError("Marks are already entered, so the exam can't go back to draft.",
                            code="marks_entered")
    with transaction.atomic():
        exam.status = Exam.Status.DRAFT
        exam.save(update_fields=["status", "updated_at"])
        drop_calendar(exam)
        log(AuditLog.Action.UPDATE, instance=exam, module=MODULE, actor=by,
            changes={"status": {"before": "scheduled", "after": "draft"}})
    return exam


def reschedule_paper(paper: ExamSubject, *, by=None) -> None:
    """A paper moved after the exam was scheduled: keep the exam's span and
    calendar event in step."""
    exam = paper.exam
    if exam.status != Exam.Status.SCHEDULED:
        return
    dates = [d for d in exam.subjects.values_list("date", flat=True) if d]
    if dates:
        exam.start_date, exam.end_date = min(dates), max(dates)
        exam.save(update_fields=["start_date", "end_date", "updated_at"])
        sync_calendar(exam)


# ---------------------------------------------------------------------------
# Who sits the exam
# ---------------------------------------------------------------------------
def candidates(exam: Exam):
    """Enrollments of the students who sit this exam: everyone in a class at
    a level with a paper, on the exam's first day."""
    day = exam.start_date or timezone.localdate()
    return (Enrollment.objects.on(day)
            .filter(section__in=sections_for(exam).filter(level__in=exam_levels(exam)))
            .select_related("student", "section__program").order_by("section__level", "section__name",
                                                                     "student__first_name", "student__last_name", "pk"))


def expected_enrollments(paper: ExamSubject, section: Section):
    """The enrollments that should have marks for ``paper`` in ``section``:
    students in the class who take the subject (electives: those who chose it)."""
    day = paper_day(paper)
    students = students_taking(section, paper.subject_id, on=day)
    return (Enrollment.objects.on(day).filter(section=section, student__in=students)
            .select_related("student").order_by("student__first_name", "student__last_name", "pk"))


# ---------------------------------------------------------------------------
# Seating
# ---------------------------------------------------------------------------
def _interleave(groups: list[list]) -> list:
    """One from each group in turn, so neighbours come from different classes."""
    ordered, index = [], 0
    while any(index < len(g) for g in groups):
        ordered.extend(g[index] for g in groups if index < len(g))
        index += 1
    return ordered


def generate_seat_plan(exam: Exam, *, strategy: str = "interleave", dry_run: bool = False, by=None) -> dict:
    """Give every candidate a room and seat. Replaces any earlier plan."""
    ensure_open_for_structure(exam)
    if exam.status == Exam.Status.DRAFT:
        raise ConflictError("Schedule the exam first: seating follows who sits it.", code="not_scheduled")
    rooms = list(exam.rooms.select_related("room").order_by("room__code"))
    if not rooms:
        raise ServiceError("Add the rooms for this exam first.", code="no_rooms")
    if any(r.seat_count is None for r in rooms):
        raise ServiceError("Every exam room needs a capacity, on the room or for this exam.", code="no_capacity")
    people = list(candidates(exam))
    available = sum(r.seat_count for r in rooms)
    if len(people) > available:
        raise ConflictError(f"{len(people)} students but only {available} seats.", code="not_enough_seats",
                            details={"students": len(people), "seats": available})
    if strategy == "interleave":
        by_section = {}
        for e in people:
            by_section.setdefault(e.section_id, []).append(e)
        people = _interleave(list(by_section.values()))
    plan, cursor = [], 0
    for exam_room in rooms:
        for seat in range(1, exam_room.seat_count + 1):
            if cursor >= len(people):
                break
            plan.append((exam_room, seat, people[cursor]))
            cursor += 1
    summary = {"students": len(people), "seats": available,
               "rooms": [{"room": r.room.name, "students": sum(1 for p in plan if p[0] == r)} for r in rooms]}
    if dry_run:
        return {**summary, "dry_run": True}
    with transaction.atomic():
        exam.seats.all().delete()
        SeatAllocation.objects.bulk_create([
            SeatAllocation(organization_id=exam.organization_id, exam=exam, enrollment=e, exam_room=r, seat_number=n)
            for r, n, e in plan])
        log(AuditLog.Action.UPDATE, instance=exam, module=MODULE, actor=by,
            metadata={"seat_plan": summary["students"]})
    return {**summary, "dry_run": False}


def check_invigilator(invigilation_paper: ExamSubject, staff, *, exclude_pk=None) -> None:
    """A person can't watch two rooms at the same time, in this exam or another."""
    if invigilation_paper.date is None:
        return
    clashes = Invigilation.objects.filter(
        staff=staff, exam_subject__date=invigilation_paper.date,
    ).exclude(pk=exclude_pk).select_related("exam_subject__subject", "exam_subject__exam")
    for other in clashes:
        if _overlap(invigilation_paper, other.exam_subject):
            raise ConflictError(
                f"{staff.full_name} is already invigilating {other.exam_subject.subject.name} "
                "at that time.", code="invigilator_clash", details={"invigilation": other.pk})


# ---------------------------------------------------------------------------
# Admit cards
# ---------------------------------------------------------------------------
def attendance_percentage(student, exam: Exam):
    """Attendance from the start of the academic year up to the first day of
    the exam, or None if none has been taken."""
    from modules.attendance.selectors import student_report

    today = org_today(exam.organization)
    start = exam.academic_year.start_date
    end = min(exam.start_date or today, today)
    if end < start:
        return None
    return student_report(student, start, end)["overall"]["percentage"]


def generate_admit_cards(exam: Exam, *, by=None, section=None) -> dict:
    """Admit cards for every candidate. Someone below the exam's attendance
    minimum gets one that's withheld, with the reason. Existing cards are left
    as they are, so running it again only adds new candidates."""
    if exam.status == Exam.Status.DRAFT:
        raise ConflictError("Schedule the exam before issuing admit cards.", code="not_scheduled")
    people = candidates(exam)
    if section is not None:
        people = people.filter(section=section)
    created = withheld = skipped = 0
    with transaction.atomic():
        locked = Exam.objects.select_for_update().get(pk=exam.pk)
        existing = set(locked.admit_cards.values_list("enrollment_id", flat=True))
        number = locked.admit_cards.count()
        for enrollment in people:
            if enrollment.pk in existing:
                skipped += 1
                continue
            number += 1
            status, reason = AdmitCard.Status.ISSUED, ""
            if exam.min_attendance_percent is not None:
                pct = attendance_percentage(enrollment.student, exam)
                if pct is not None and Decimal(str(pct)) < exam.min_attendance_percent:
                    status = AdmitCard.Status.WITHHELD
                    reason = f"Attendance {pct}% is below the required {exam.min_attendance_percent}%."
                    withheld += 1
            AdmitCard.objects.create(
                organization_id=exam.organization_id, exam=exam, enrollment=enrollment, student=enrollment.student,
                card_number=f"EX{exam.pk}-{number:04d}", status=status, withheld_reason=reason, issued_by=by)
            created += 1
    return {"created": created, "withheld": withheld, "skipped": skipped}


def withhold_card(card: AdmitCard, reason: str, *, by=None) -> AdmitCard:
    if not reason.strip():
        raise ServiceError("Say why the admit card is withheld.", code="reason_required")
    card.status, card.withheld_reason = AdmitCard.Status.WITHHELD, reason.strip()
    card.save(update_fields=["status", "withheld_reason", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=card, module=MODULE, actor=by,
        changes={"status": {"before": "issued", "after": "withheld"}}, metadata={"reason": reason.strip()})
    return card


def release_card(card: AdmitCard, *, by=None) -> AdmitCard:
    card.status, card.withheld_reason = AdmitCard.Status.ISSUED, ""
    card.issued_by = by
    card.save(update_fields=["status", "withheld_reason", "issued_by", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=card, module=MODULE, actor=by,
        changes={"status": {"before": "withheld", "after": "issued"}})
    return card


# ---------------------------------------------------------------------------
# Mark sheets and marks
# ---------------------------------------------------------------------------
def teaches(user, section, subject_id) -> bool:
    staff = staff_member_for_user(user)
    return staff is not None and TeachingAssignment.objects.filter(
        teacher=staff, section=section, subject_id=subject_id, is_active=True).exists()


def can_enter(user, sheet: MarkSheet) -> bool:
    """The exam office, or a teacher of this subject in this class."""
    campus_id = sheet.section.campus_id
    if holds(user, MANAGE, campus_id):
        return True
    return holds(user, MARK, campus_id) and teaches(user, sheet.section, sheet.exam_subject.subject_id)


def ensure_can_enter(user, sheet: MarkSheet) -> None:
    if not can_enter(user, sheet):
        raise PermissionDeniedError("Only a teacher of this subject in this class, or the exam office, "
                                    "can enter these marks.", code="not_your_class")


def open_sheet(paper: ExamSubject, section: Section, *, by) -> tuple[MarkSheet, bool]:
    """The mark sheet for a paper and class, created on first use. Idempotent."""
    exam = paper.exam
    if exam.status == Exam.Status.DRAFT:
        raise ConflictError("The exam isn't scheduled yet.", code="not_scheduled")
    if (section.campus_id, section.program_id, section.academic_year_id, section.level) != (
            exam.campus_id, exam.program_id, exam.academic_year_id, paper.level):
        raise ServiceError("That class doesn't sit this paper.", code="wrong_section")
    if paper.date > org_today(exam.organization):
        raise ServiceError("The paper hasn't been sat yet.", code="future_paper")
    sheet = MarkSheet.objects.filter(exam_subject=paper, section=section).first()
    created = sheet is None
    if created:
        sheet = MarkSheet(organization_id=exam.organization_id, exam_subject=paper, section=section)
    ensure_can_enter(by, sheet)
    if created:
        sheet.save()
    return sheet, created


@dataclass
class MarkEntry:
    enrollment_id: int
    component_id: int
    status: str = MarkStatus.PRESENT
    marks: Decimal | None = None


def _check_entry(entry: MarkEntry, component: ExamComponent) -> None:
    if entry.status == MarkStatus.PRESENT:
        if entry.marks is None:
            raise ServiceError("A present student needs marks.", code="marks_required")
        if entry.marks < 0 or entry.marks > component.full_marks:
            raise ServiceError(f"{component.name} marks must be between 0 and {component.full_marks}.",
                               code="marks_out_of_range")
    elif entry.marks is not None:
        raise ServiceError(f"A student who is {entry.status} has no marks.", code="marks_not_allowed")


def enter_marks(*, sheet: MarkSheet, entries: list[MarkEntry], by, reason: str = "") -> list[Mark]:
    """Enter or change marks. While the sheet is open that's free. Once it is
    submitted or verified only the office may change a mark, with a reason,
    and the old value is kept as a correction (results are refreshed if the
    exam is already published)."""
    from . import results

    with transaction.atomic():
        sheet = MarkSheet.objects.select_for_update().select_related(
            "exam_subject__exam", "exam_subject__subject", "section").get(pk=sheet.pk)
        exam = sheet.exam_subject.exam
        ensure_can_enter(by, sheet)
        if exam.status == Exam.Status.DRAFT:
            raise ConflictError("The exam isn't scheduled yet.", code="not_scheduled")
        locked = not sheet.is_open
        if locked:
            if not holds(by, MANAGE, sheet.section.campus_id):
                raise ConflictError("These marks are submitted. Ask the exam office to send the sheet "
                                    "back or to correct a mark.", code="sheet_locked")
            if not reason.strip():
                raise ServiceError("Give a reason for changing marks after submission.", code="reason_required")

        components = {c.pk: c for c in sheet.exam_subject.components.all()}
        allowed = {e.pk for e in expected_enrollments(sheet.exam_subject, sheet.section)}
        seen = set()
        for entry in entries:
            if entry.component_id not in components:
                raise ServiceError("That component isn't part of this paper.", code="bad_component")
            if entry.enrollment_id not in allowed:
                raise ServiceError("That student doesn't sit this paper in this class.", code="not_expected",
                                   details={"enrollment": entry.enrollment_id})
            if (entry.enrollment_id, entry.component_id) in seen:
                raise ServiceError("A student is listed twice for the same component.", code="duplicate")
            seen.add((entry.enrollment_id, entry.component_id))
            _check_entry(entry, components[entry.component_id])

        existing = {(m.enrollment_id, m.component_id): m for m in sheet.marks.select_for_update()}
        saved, changed = [], set()
        for entry in entries:
            marks = entry.marks if entry.status == MarkStatus.PRESENT else None
            mark = existing.get((entry.enrollment_id, entry.component_id))
            if mark is None:
                mark = Mark.objects.create(
                    organization_id=sheet.organization_id, sheet=sheet, component_id=entry.component_id,
                    enrollment_id=entry.enrollment_id, status=entry.status, marks=marks, entered_by=by)
                if locked:
                    log(AuditLog.Action.CREATE, instance=mark, module=MODULE, actor=by,
                        metadata={"correction": True, "reason": reason.strip()})
                    changed.add(entry.enrollment_id)
            elif (mark.status, mark.marks) != (entry.status, marks):
                if locked:
                    MarkCorrection.objects.create(
                        organization_id=sheet.organization_id, mark=mark, old_status=mark.status,
                        old_marks=mark.marks, new_status=entry.status, new_marks=marks,
                        reason=reason.strip(), corrected_by=by)
                    log(AuditLog.Action.UPDATE, instance=mark, module=MODULE, actor=by,
                        changes={"marks": {"before": str(mark.marks), "after": str(marks)},
                                 "status": {"before": mark.status, "after": entry.status}},
                        metadata={"correction": True, "reason": reason.strip()})
                    changed.add(entry.enrollment_id)
                mark.status, mark.marks, mark.entered_by = entry.status, marks, by
                mark.save(update_fields=["status", "marks", "entered_by", "updated_at"])
            saved.append(mark)
        if changed and exam.is_published:
            results.refresh_results(exam, sorted(changed), by=by)
    return saved


def missing_marks(sheet: MarkSheet) -> list[dict]:
    """Students and components without a mark yet."""
    have = set(sheet.marks.values_list("enrollment_id", "component_id"))
    components = list(sheet.exam_subject.components.all())
    missing = []
    for enrollment in expected_enrollments(sheet.exam_subject, sheet.section):
        gaps = [c.name for c in components if (enrollment.pk, c.pk) not in have]
        if gaps:
            missing.append({"enrollment": enrollment.pk, "student": enrollment.student.full_name, "missing": gaps})
    return missing


def submit_sheet(sheet: MarkSheet, *, by) -> MarkSheet:
    """The teacher is done. Everyone needs a mark (or absent/exempt) first."""
    with transaction.atomic():
        sheet = MarkSheet.objects.select_for_update().select_related(
            "exam_subject__exam", "exam_subject__subject", "section").get(pk=sheet.pk)
        ensure_can_enter(by, sheet)
        if sheet.status != MarkSheet.Status.OPEN:
            raise ConflictError("These marks are already submitted.", code="already_submitted")
        missing = missing_marks(sheet)
        if missing:
            raise ConflictError(f"{len(missing)} student(s) still have no mark.", code="unmarked",
                                details={"missing": missing})
        sheet.status = MarkSheet.Status.SUBMITTED
        sheet.submitted_at, sheet.submitted_by, sheet.review_note = timezone.now(), by, ""
        sheet.save(update_fields=["status", "submitted_at", "submitted_by", "review_note", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=sheet, module=MODULE, actor=by,
            changes={"status": {"before": "open", "after": "submitted"}})
    return sheet


def verify_sheet(sheet: MarkSheet, *, by) -> MarkSheet:
    with transaction.atomic():
        sheet = MarkSheet.objects.select_for_update().get(pk=sheet.pk)
        if sheet.status == MarkSheet.Status.VERIFIED:
            return sheet
        if sheet.status != MarkSheet.Status.SUBMITTED:
            raise ConflictError("Only submitted marks can be verified.", code="not_submitted")
        sheet.status = MarkSheet.Status.VERIFIED
        sheet.verified_at, sheet.verified_by = timezone.now(), by
        sheet.save(update_fields=["status", "verified_at", "verified_by", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=sheet, module=MODULE, actor=by,
            changes={"status": {"before": "submitted", "after": "verified"}})
    return sheet


def send_back(sheet: MarkSheet, reason: str, *, by) -> MarkSheet:
    """The office returns a sheet to the teacher to redo. Not once results
    are published: from then on, changes are corrections."""
    if not reason.strip():
        raise ServiceError("Say what needs another look.", code="reason_required")
    with transaction.atomic():
        sheet = MarkSheet.objects.select_for_update().select_related("exam_subject__exam").get(pk=sheet.pk)
        if sheet.exam_subject.exam.is_published:
            raise ConflictError("The results are published: correct individual marks instead.",
                                code="exam_published")
        if sheet.is_open:
            raise ConflictError("These marks are still open.", code="already_open")
        before = sheet.status
        sheet.status, sheet.review_note = MarkSheet.Status.OPEN, reason.strip()
        sheet.verified_at = sheet.verified_by = None
        sheet.save(update_fields=["status", "review_note", "verified_at", "verified_by", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=sheet, module=MODULE, actor=by,
            changes={"status": {"before": before, "after": "open"}}, metadata={"reason": reason.strip()})
    return sheet
