"""Attendance reads: summaries, the class register, defaulters, what's
missing, and staff reports.

Percentages count present, late and on duty as attended. Excused, leave
and medical leave are left out of the count altogether, so a student on
sick leave isn't marked down for it: attended / (sessions − excused).
"""
from collections import Counter, defaultdict
from datetime import date as Date, timedelta

from django.db.models import Count, Q

from modules.academics.models import Program
from modules.academics.selectors import closed_days
from modules.students.models import Enrollment
from modules.timetable.services import lessons_on

from .models import (
    ATTENDED,
    EXCUSED,
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
    StaffAttendanceDay,
)
from .services import org_today, schedule_for, school_day_problem, works_on


def summarize(counts: dict) -> dict:
    """Counts per status, the total and the percentage, from {status: n}."""
    total = sum(counts.values())
    attended = sum(counts.get(s, 0) for s in ATTENDED)
    excused = sum(counts.get(s, 0) for s in EXCUSED)
    counted = total - excused
    return {
        "total": total,
        **{status: counts.get(status, 0) for status in AttendanceStatus.values},
        "attended": attended,
        "percentage": round(100 * attended / counted, 1) if counted else None,
    }


def _counts(records) -> dict:
    return dict(records.values_list("status").annotate(n=Count("pk")).order_by())


def records_between(start: Date, end: Date, queryset=None):
    queryset = AttendanceRecord.objects.all() if queryset is None else queryset
    return queryset.filter(session__date__gte=start, session__date__lte=end)


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------
def student_report(student, start: Date, end: Date, records=None) -> dict:
    """A student's attendance over a span: overall, and per subject for
    lessons."""
    records = records_between(start, end, records).filter(enrollment__student=student)
    subjects = []
    lesson_records = records.filter(session__kind=AttendanceSession.Kind.LESSON)
    rows = (lesson_records.values("session__timetable_entry__teaching_assignment__subject",
                                  "session__timetable_entry__teaching_assignment__subject__name",
                                  "status").annotate(n=Count("pk")).order_by())
    per_subject = defaultdict(dict)
    names = {}
    for row in rows:
        subject_id = row["session__timetable_entry__teaching_assignment__subject"]
        names[subject_id] = row["session__timetable_entry__teaching_assignment__subject__name"]
        per_subject[subject_id][row["status"]] = row["n"]
    for subject_id, counts in sorted(per_subject.items(), key=lambda x: names[x[0]]):
        subjects.append({"subject": subject_id, "subject_name": names[subject_id], **summarize(counts)})
    return {
        "student": student.pk, "student_name": student.full_name,
        "from": start.isoformat(), "to": end.isoformat(),
        "overall": summarize(_counts(records)),
        "daily": summarize(_counts(records.filter(session__kind=AttendanceSession.Kind.DAILY))),
        "subjects": subjects,
    }


def section_register(section, start: Date, end: Date) -> dict:
    """The class register: one row per student who was in the class during
    the span, one column per session, and each student's totals."""
    sessions = list(AttendanceSession.objects.filter(section=section, date__gte=start, date__lte=end)
                    .select_related("timetable_entry__teaching_assignment__subject",
                                    "timetable_entry__period")
                    .order_by("date", "timetable_entry__period__start_time", "pk"))
    enrollments = list(
        Enrollment.objects.filter(section=section, started_on__lte=end)
        .filter(Q(ended_on__isnull=True) | Q(ended_on__gt=start))
        .select_related("student").order_by("student__first_name", "student__last_name", "pk")
    )
    marks = defaultdict(dict)
    for record in AttendanceRecord.objects.filter(session__in=sessions).only(
            "session_id", "enrollment_id", "status"):
        marks[record.enrollment_id][record.session_id] = record.status

    columns = [{
        "session": s.pk, "date": s.date.isoformat(), "kind": s.kind, "status": s.status,
        "subject_name": (s.timetable_entry.teaching_assignment.subject.name
                         if s.timetable_entry_id else None),
        "start_time": (s.timetable_entry.period.start_time.isoformat()
                       if s.timetable_entry_id else None),
    } for s in sessions]
    rows = []
    for enrollment in enrollments:
        statuses = marks.get(enrollment.pk, {})
        rows.append({
            "enrollment": enrollment.pk, "student": enrollment.student_id,
            "student_name": enrollment.student.full_name,
            "student_number": enrollment.student.student_number,
            "marks": [statuses.get(s.pk) for s in sessions],
            **summarize(Counter(statuses.values())),
        })
    return {"section": section.pk, "section_name": section.display_name,
            "from": start.isoformat(), "to": end.isoformat(), "sessions": columns, "students": rows}


def defaulters(sections, start: Date, end: Date, below: float) -> list[dict]:
    """Students whose attendance over the span is under ``below`` percent."""
    rows = (records_between(start, end).filter(session__section__in=sections)
            .values("enrollment__student", "enrollment__student__first_name",
                    "enrollment__student__last_name", "enrollment__student__student_number",
                    "session__section", "status")
            .annotate(n=Count("pk")).order_by())
    per_student = defaultdict(Counter)
    info = {}
    for row in rows:
        key = (row["enrollment__student"], row["session__section"])
        per_student[key][row["status"]] += row["n"]
        info[key] = row
    result = []
    for key, counts in per_student.items():
        summary = summarize(counts)
        if summary["percentage"] is not None and summary["percentage"] < below:
            row = info[key]
            result.append({
                "student": key[0], "section": key[1],
                "student_name": f"{row['enrollment__student__first_name']} "
                                f"{row['enrollment__student__last_name']}".strip(),
                "student_number": row["enrollment__student__student_number"],
                **summary,
            })
    return sorted(result, key=lambda r: (r["percentage"], r["student_name"]))


def missing(day: Date, *, organization_id, sections) -> list[dict]:
    """What should have been taken on ``day`` and wasn't submitted: every
    lesson that ran (for lesson-mode programs) and every class's roll call
    (daily mode)."""
    submitted = AttendanceSession.objects.filter(organization_id=organization_id, date=day,
                                                 status=AttendanceSession.Status.SUBMITTED)
    open_ = {(s.kind, s.timetable_entry_id or s.section_id): s
             for s in AttendanceSession.objects.filter(organization_id=organization_id, date=day,
                                                       status=AttendanceSession.Status.OPEN)}
    done_lessons = set(submitted.filter(kind="lesson").values_list("timetable_entry_id", flat=True))
    done_days = set(submitted.filter(kind="daily").values_list("section_id", flat=True))
    sections = sections.filter(academic_year__start_date__lte=day,
                               academic_year__end_date__gte=day).select_related("program", "class_teacher")
    result = []

    lesson_sections = set(sections.filter(program__attendance_mode=Program.AttendanceMode.LESSON)
                          .values_list("pk", flat=True))
    for lesson in lessons_on(day, organization_id=organization_id):
        entry = lesson.entry
        section = entry.teaching_assignment.section
        if (lesson.is_cancelled or entry.pk in done_lessons
                or section.pk not in lesson_sections):
            continue
        session = open_.get(("lesson", entry.pk))
        result.append({
            "kind": "lesson", "section": section.pk, "section_name": section.display_name,
            "timetable_entry": entry.pk, "subject_name": entry.teaching_assignment.subject.name,
            "start_time": entry.period.start_time.isoformat(),
            "teacher": lesson.teacher.pk, "teacher_name": lesson.teacher.full_name,
            "session": session.pk if session else None,
        })

    for section in sections.filter(program__attendance_mode=Program.AttendanceMode.DAILY):
        if section.pk in done_days or school_day_problem(section, day):
            continue
        if not Enrollment.objects.on(day).filter(section=section).exists():
            continue
        session = open_.get(("daily", section.pk))
        teacher = section.class_teacher
        result.append({
            "kind": "daily", "section": section.pk, "section_name": section.display_name,
            "timetable_entry": None, "subject_name": None, "start_time": None,
            "teacher": teacher.pk if teacher else None,
            "teacher_name": teacher.full_name if teacher else None,
            "session": session.pk if session else None,
        })
    return result


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------
def staff_report(staff_members, start: Date, end: Date) -> list[dict]:
    """Per staff member: days by status, and absences — working days up to
    today with no record at all."""
    end_counted = min(end, org_today(staff_members[0].organization)) if staff_members else end
    days = defaultdict(dict)
    for row in StaffAttendanceDay.objects.filter(staff__in=staff_members, date__gte=start,
                                                 date__lte=end).values("staff_id", "date", "status",
                                                                       "worked_minutes"):
        days[row["staff_id"]][row["date"]] = row

    closed_by_campus = {}
    result = []
    for staff in staff_members:
        if staff.campus_id not in closed_by_campus:
            closed_by_campus[staff.campus_id] = closed_days(staff.campus, start, end_counted)
        closed = closed_by_campus[staff.campus_id]
        schedule = schedule_for(staff)
        own = days.get(staff.pk, {})
        counts = Counter(row["status"] for row in own.values())
        working, unrecorded = 0, 0
        day = start
        while day <= end_counted:
            if day not in closed and works_on(staff, day, schedule):
                working += 1
                if day not in own:
                    unrecorded += 1
            day += timedelta(days=1)
        worked = [row["worked_minutes"] for row in own.values() if row["worked_minutes"]]
        result.append({
            "staff": staff.pk, "staff_name": staff.full_name,
            "employee_number": staff.employee_number,
            "working_days": working,
            **{status: counts.get(status, 0) for status in StaffAttendanceDay.Status.values},
            "absent": counts.get("absent", 0) + unrecorded,
            "average_worked_minutes": round(sum(worked) / len(worked)) if worked else None,
        })
    return result
