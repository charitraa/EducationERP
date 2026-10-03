"""Read-side: report cards, transcripts and exam summaries.

Everything a client needs to print or show is returned as plain data. Turning
it into a PDF is left to the client, so this layer stays testable.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q

from modules.attendance.selectors import student_report
from modules.attendance.services import org_today

from . import grading
from .models import AdmitCard, Exam, Result, ResultPlan, SeatAllocation


def num(value):
    """Decimals as plain numbers in the JSON."""
    return None if value is None else float(value)


def published_results():
    """Results whose exam or term result is published: the only ones a
    student or parent may see."""
    return Result.objects.filter(
        Q(exam__status=Exam.Status.PUBLISHED, exam__deleted_at__isnull=True)
        | Q(plan__status=ResultPlan.Status.PUBLISHED, plan__deleted_at__isnull=True))


def _lines(result: Result) -> list[dict]:
    return [{
        "subject": line.subject_id, "subject_name": line.subject.name, "subject_code": line.subject.code,
        "credit_hours": num(line.credit_hours), "obtained": num(line.obtained), "full": num(line.full),
        "percentage": num(line.percentage), "letter": line.letter, "grade_point": num(line.grade_point),
        "remark": line.remark, "status": line.status, "absent": line.absent, "detail": line.detail,
    } for line in result.subjects.select_related("subject").order_by("subject__name")]


def _window(result: Result):
    """The dates attendance is summarized over: from the start of the term
    (or academic year) to the end of the exam, or today if that's earlier."""
    source = result.exam or result.plan
    today = org_today(result.organization)
    start = source.term.start_date if source.term_id else source.academic_year.start_date
    if result.exam_id:
        end = result.exam.end_date or today
    else:
        ends = [i.exam.end_date for i in result.plan.items.select_related("exam") if i.exam.end_date]
        end = max(ends) if ends else today
    return start, min(end, today)


def report_card(result: Result) -> dict:
    """A student's report card for one exam or term result."""
    source = result.exam or result.plan
    section, student = result.section, result.student
    start, end = _window(result)
    attendance = student_report(student, start, end)["overall"] if start <= end else None
    class_size = Result.objects.filter(exam=result.exam, plan=result.plan, section=section).count()
    scale = grading.load_scale(source.grade_scale)
    return {
        "result_id": result.pk,
        "kind": "exam" if result.exam_id else "term",
        "title": source.name,
        "exam_type": result.exam.exam_type.name if result.exam_id else None,
        "academic_year": source.academic_year.name,
        "term": source.term.name if source.term_id else None,
        "published_at": source.published_at,
        "student": {
            "id": student.pk, "name": student.full_name, "student_number": student.student_number,
            "date_of_birth": student.date_of_birth, "gender": student.gender,
        },
        "campus": section.campus.name,
        "program": section.program.name,
        "level": section.level,
        "level_label": section.program.level_label(section.level),
        "section": section.name,
        "subjects": _lines(result),
        "total_obtained": num(result.total_obtained), "total_full": num(result.total_full),
        "percentage": num(result.percentage), "grade_point": num(result.grade_point),
        "letter": result.letter, "division": result.division, "result": result.status,
        "rank_in_section": result.rank_in_section, "rank_in_level": result.rank_in_level,
        "class_size": class_size,
        "attendance": None if attendance is None else {
            "from": start.isoformat(), "to": end.isoformat(), "total": attendance["total"],
            "attended": attendance["attended"], "percentage": attendance["percentage"]},
        "remark": result.remark,
        "grading": [{"from": num(b.min_percentage), "letter": b.letter, "grade_point": num(b.grade_point),
                     "remark": b.remark, "pass": b.is_pass} for b in scale.bands],
    }


def _when(result: Result):
    """When a result belongs on the timeline: the exam's last day, or the
    latest exam a term result counts."""
    if result.exam_id:
        return result.exam.end_date or result.exam.academic_year.start_date
    ends = [i.exam.end_date for i in result.plan.items.select_related("exam") if i.exam.end_date]
    return max(ends) if ends else result.plan.academic_year.start_date


def transcript(student) -> dict:
    """Every published result marked for the transcript, oldest first, with a
    cumulative GPA. Retakes aren't modeled: every line counts."""
    results = (published_results().filter(student=student)
               .filter(Q(exam__on_transcript=True) | Q(plan__on_transcript=True))
               .select_related("exam__academic_year", "plan__academic_year", "section__program",
                               "section__academic_year", "section__campus"))
    ordered = sorted(results, key=lambda r: ((r.exam or r.plan).academic_year.start_date, _when(r), r.pk))
    records, weighted, credits, earned = [], Decimal("0"), Decimal("0"), Decimal("0")
    for result in ordered:
        source = result.exam or result.plan
        lines = _lines(result)
        for line in result.subjects.all():
            if line.status in ("pass", "fail"):
                weighted += line.grade_point * line.credit_hours
                credits += line.credit_hours
                if line.status == "pass":
                    earned += line.credit_hours
        records.append({
            "result_id": result.pk, "kind": "exam" if result.exam_id else "term", "title": source.name,
            "academic_year": source.academic_year.name, "program": result.section.program.name,
            "level_label": result.section.program.level_label(result.section.level),
            "campus": result.section.campus.name, "subjects": lines, "percentage": num(result.percentage),
            "grade_point": num(result.grade_point), "letter": result.letter, "division": result.division,
            "result": result.status,
        })
    return {
        "student": {"id": student.pk, "name": student.full_name, "student_number": student.student_number,
                    "date_of_birth": student.date_of_birth, "gender": student.gender},
        "records": records,
        "cumulative": {"credits": num(credits), "credits_passed": num(earned),
                       "gpa": num(grading.q2(weighted / credits)) if credits else None},
    }


def exam_summary(exam: Exam) -> dict:
    """How the class did: pass rates, averages, and the toppers."""
    results = list(Result.objects.filter(exam=exam).select_related("student", "section__program"))
    graded = [r for r in results if r.status in ("pass", "fail")]
    by_status = defaultdict(int)
    for r in results:
        by_status[r.status] += 1
    per_subject = defaultdict(lambda: {"students": 0, "passed": 0, "total": Decimal("0"), "highest": None,
                                       "lowest": None, "absent": 0})
    from .models import SubjectResult

    for line in SubjectResult.objects.filter(result__exam=exam, status__in=("pass", "fail")).select_related("subject"):
        row = per_subject[line.subject.name]
        row["students"] += 1
        row["passed"] += line.status == "pass"
        row["absent"] += line.absent
        row["total"] += line.percentage
        row["highest"] = line.percentage if row["highest"] is None else max(row["highest"], line.percentage)
        row["lowest"] = line.percentage if row["lowest"] is None else min(row["lowest"], line.percentage)
    subjects = [{
        "subject_name": name, "students": r["students"], "passed": r["passed"], "absent": r["absent"],
        "pass_rate": num(grading.q2(Decimal(r["passed"]) * 100 / r["students"])),
        "average": num(grading.q2(r["total"] / r["students"])), "highest": num(r["highest"]),
        "lowest": num(r["lowest"]),
    } for name, r in sorted(per_subject.items())]
    toppers = sorted((r for r in graded if r.status == "pass"), key=lambda r: -r.percentage)[:10]
    return {
        "exam": exam.pk, "name": exam.name, "results": len(results), "by_status": dict(by_status),
        "pass_rate": num(grading.q2(Decimal(by_status["pass"]) * 100 / len(graded))) if graded else None,
        "average_percentage": num(grading.q2(sum((r.percentage for r in graded), Decimal("0")) / len(graded)))
        if graded else None,
        "subjects": subjects,
        "toppers": [{"student": r.student_id, "student_name": r.student.full_name,
                     "section_name": r.section.display_name, "percentage": num(r.percentage),
                     "rank_in_level": r.rank_in_level} for r in toppers],
    }


def admit_card_data(card: AdmitCard) -> dict:
    """What an admit card shows: the student, the papers with dates and
    times, and where they sit."""
    exam = card.exam
    seat = SeatAllocation.objects.filter(exam=exam, enrollment=card.enrollment).select_related(
        "exam_room__room").first()
    level = card.enrollment.section.level
    papers = (exam.subjects.filter(level=level).select_related("subject").order_by("date", "start_time"))
    student = card.student
    return {
        "card_number": card.card_number, "status": card.status, "withheld_reason": card.withheld_reason,
        "exam": {"id": exam.pk, "name": exam.name, "type": exam.exam_type.name,
                 "start_date": exam.start_date, "end_date": exam.end_date, "instructions": exam.instructions},
        "student": {"id": student.pk, "name": student.full_name, "student_number": student.student_number},
        "campus": exam.campus.name, "program": exam.program.name,
        "section": card.enrollment.section.display_name,
        "seat": None if seat is None else {"room": seat.exam_room.room.name, "seat_number": seat.seat_number},
        "papers": [{"subject_name": p.subject.name, "date": p.date, "start_time": p.start_time,
                    "end_time": p.end_time} for p in papers],
    }
