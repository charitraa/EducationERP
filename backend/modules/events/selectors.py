"""Read-side: rosters, leaderboards, and a student's own summary."""
from .models import EventAttendance, EventParticipation, EventRegistration, RegistrationStatus, StudentAward


def event_roster(event) -> list[dict]:
    """Everyone signed up (or, for a no-sign-up event, everyone marked so
    far), with their attendance and participation."""
    registrations = {r.student_id: r for r in event.registrations.exclude(
        status=RegistrationStatus.WITHDRAWN).select_related("student")}
    attendance = {a.student_id: a for a in event.attendance.all()}
    participation = {}
    for p in event.participation.select_related("student"):
        participation.setdefault(p.student_id, []).append(p)

    student_ids = set(registrations) | set(attendance) | set(participation)
    rows = []
    for student_id in student_ids:
        registration = registrations.get(student_id)
        record = attendance.get(student_id)
        student = (registration.student if registration else record.student if record else
                  participation[student_id][0].student)
        rows.append({
            "student": student_id, "student_name": student.full_name, "student_number": student.student_number,
            "registration_status": registration.status if registration else None,
            "attendance_status": record.status if record else None,
            "roles": [p.role for p in participation.get(student_id, [])],
        })
    rows.sort(key=lambda r: r["student_name"])
    return rows


def student_summary(student) -> dict:
    """A student's points total, their awards, and the events they've taken
    part in."""
    from .models import StudentPoints

    totals = StudentPoints.objects.filter(student=student).first()
    awards = StudentAward.objects.filter(student=student, ended_on__isnull=True).select_related("award")
    attended = EventAttendance.objects.filter(student=student, status="present").select_related(
        "event__category").order_by("-event__start_at")
    participated = EventParticipation.objects.filter(student=student).select_related("event__category")
    return {
        "student": student.pk, "student_name": student.full_name,
        "points": totals.total if totals else 0,
        "awards": [{"award": a.award_id, "name": a.award.name, "kind": a.award.kind, "awarded_at": a.awarded_at}
                   for a in awards],
        "events_attended": attended.count(),
        "events": [{"event": a.event_id, "name": a.event.name, "category": a.event.category.name,
                   "start_at": a.event.start_at} for a in attended[:20]],
        "roles": [{"event": p.event_id, "name": p.event.name, "role": p.role, "position": p.position}
                 for p in participated],
    }


def leaderboard(organization_id, campus_ids=None, limit=20) -> list[dict]:
    """Students with the most points."""
    from .models import StudentPoints

    totals = StudentPoints.objects.filter(organization_id=organization_id, total__gt=0).select_related(
        "student__campus").order_by("-total")
    if campus_ids is not None:
        totals = totals.filter(student__campus_id__in=campus_ids)
    rows = []
    for rank, row in enumerate(totals[:limit], start=1):
        rows.append({"rank": rank, "student": row.student_id, "student_name": row.student.full_name,
                    "student_number": row.student.student_number, "points": row.total})
    return rows
