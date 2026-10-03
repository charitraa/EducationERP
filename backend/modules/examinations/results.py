"""Turning marks into stored results, publishing them, and combining exams
into term results.

Computing is idempotent: the same marks always give the same result, so it is
safe to run again after a correction. Stored results are what students see.
"""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, ServiceError

from . import grading, services
from .models import Exam, Mark, MarkSheet, Result, ResultPlan, SubjectResult

MODULE = services.MODULE


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------
def expected_sheets(exam: Exam) -> list[tuple]:
    """(paper, section) pairs that should have a mark sheet: every class at
    the paper's level that has students taking the subject."""
    pairs = []
    for paper in exam.subjects.select_related("subject"):
        for section in services.sections_for(exam, paper.level).select_related("program"):
            if services.expected_enrollments(paper, section).exists():
                pairs.append((paper, section))
    return pairs


def readiness(exam: Exam) -> dict:
    """What still stands between the exam and publishing its results."""
    sheets = {(s.exam_subject_id, s.section_id): s
              for s in MarkSheet.objects.filter(exam_subject__exam=exam)}
    missing, unverified = [], []
    pairs = expected_sheets(exam)
    for paper, section in pairs:
        label = {"exam_subject": paper.pk, "subject": paper.subject.name, "section": section.pk,
                 "section_name": section.display_name}
        sheet = sheets.get((paper.pk, section.pk))
        if sheet is None:
            missing.append(label)
        elif sheet.status != MarkSheet.Status.VERIFIED:
            unverified.append({**label, "sheet": sheet.pk, "status": sheet.status})
    return {"ready": not missing and not unverified, "expected_sheets": len(pairs),
            "not_started": missing, "not_verified": unverified}


# ---------------------------------------------------------------------------
# One exam
# ---------------------------------------------------------------------------
def _replace_lines(result: Result, outcomes: list[grading.SubjectOutcome]) -> None:
    result.subjects.all().delete()
    SubjectResult.objects.bulk_create([
        SubjectResult(
            result=result, subject_id=o.subject_id, exam_subject_id=o.exam_subject_id,
            credit_hours=o.credit, obtained=o.obtained, full=o.full, percentage=o.percentage,
            letter=o.band.letter if o.band else "", grade_point=o.band.grade_point if o.band else Decimal("0"),
            remark=o.band.remark if o.band else "", status=o.status, absent=o.absent, detail=o.detail)
        for o in outcomes if o.status != "exempt"])


def _store(result: Result, overall: grading.Overall) -> None:
    result.total_obtained, result.total_full = overall.total_obtained, overall.total_full
    result.percentage, result.grade_point = overall.percentage, overall.grade_point
    result.letter, result.division, result.status = overall.letter, overall.division, overall.status
    result.save()


def _rank(source_filter: dict) -> None:
    """Rank passing students within their section and within their level.
    Only passes rank: a failed result has no place."""
    results = list(Result.objects.filter(**source_filter).select_related("section"))
    passed = [r for r in results if r.status == "pass"]
    section_ranks, level_ranks = {}, {}
    by_section, by_level = defaultdict(list), defaultdict(list)
    for r in passed:
        by_section[r.section_id].append((r.pk, r.percentage))
        by_level[r.section.level].append((r.pk, r.percentage))
    for group in by_section.values():
        section_ranks.update(grading.rank(group))
    for group in by_level.values():
        level_ranks.update(grading.rank(group))
    for r in results:
        rank_section, rank_level = section_ranks.get(r.pk), level_ranks.get(r.pk)
        if (r.rank_in_section, r.rank_in_level) != (rank_section, rank_level):
            r.rank_in_section, r.rank_in_level = rank_section, rank_level
            r.save(update_fields=["rank_in_section", "rank_in_level", "updated_at"])


def compute_exam_results(exam: Exam, *, only_students=None) -> dict:
    """(Re)compute results from the marks. ``only_students`` limits it to
    some students (after a correction); ranks are always redone for all."""
    scale = grading.load_scale(exam.grade_scale)
    papers = list(exam.subjects.select_related("subject").prefetch_related("components"))
    marks = {(m.enrollment_id, m.component_id): m
             for m in Mark.objects.filter(sheet__exam_subject__exam=exam)}

    # student -> [(paper, enrollment)]: the papers each student should have.
    plan_of = defaultdict(list)
    for paper in papers:
        for section in services.sections_for(exam, paper.level):
            for enrollment in services.expected_enrollments(paper, section):
                plan_of[enrollment.student_id].append((paper, enrollment))

    counts = defaultdict(int)
    with transaction.atomic():
        for student_id, items in plan_of.items():
            if only_students is not None and student_id not in only_students:
                continue
            outcomes = []
            for paper, enrollment in items:
                inputs = []
                for c in paper.components.all():
                    m = marks.get((enrollment.pk, c.pk))
                    inputs.append(grading.ComponentInput(c.name, c.kind, c.full_marks, c.pass_marks,
                                                         m.status if m else None, m.marks if m else None))
                outcomes.append(grading.grade_subject(
                    scale, subject_id=paper.subject_id, exam_subject_id=paper.pk, credit=paper.weight,
                    components=inputs))
            canonical = items[0][1]
            overall = grading.combine(scale, outcomes)
            result, _ = Result.objects.get_or_create(
                exam=exam, enrollment=canonical,
                defaults={"organization_id": exam.organization_id, "student_id": student_id,
                          "section_id": canonical.section_id})
            # A student who moved class between papers keeps one result.
            Result.objects.filter(exam=exam, student_id=student_id).exclude(pk=result.pk).delete()
            _store(result, overall)
            _replace_lines(result, outcomes)
            counts[overall.status] += 1
        if only_students is None:
            Result.objects.filter(exam=exam).exclude(student_id__in=list(plan_of)).delete()
        _rank({"exam": exam})
    return dict(counts)


def publish_exam(exam: Exam, *, by) -> dict:
    if exam.status == Exam.Status.PUBLISHED:
        raise ConflictError("The results are already published.", code="already_published")
    if exam.status != Exam.Status.SCHEDULED:
        raise ConflictError("Schedule the exam before publishing results.", code="not_scheduled")
    with transaction.atomic():
        exam = Exam.objects.select_for_update().select_related("grade_scale", "academic_year").get(pk=exam.pk)
        state = readiness(exam)
        if not state["ready"]:
            raise ConflictError("Not every mark sheet is verified yet.", code="not_ready", details=state)
        counts = compute_exam_results(exam)
        if counts.get("incomplete"):
            raise ConflictError(f"{counts['incomplete']} result(s) are missing marks.", code="incomplete_marks",
                                details={"results": counts})
        exam.status, exam.published_at, exam.published_by = Exam.Status.PUBLISHED, timezone.now(), by
        exam.save(update_fields=["status", "published_at", "published_by", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=exam, module=MODULE, actor=by,
            changes={"status": {"before": "scheduled", "after": "published"}}, metadata={"results": counts})
    _notify_results_published(Result.objects.filter(exam=exam), exam.name, exam.organization_id)
    return counts


def unpublish_exam(exam: Exam, reason: str, *, by) -> Exam:
    if exam.status != Exam.Status.PUBLISHED:
        raise ConflictError("The results aren't published.", code="not_published")
    if not reason.strip():
        raise ServiceError("Say why the results are being withdrawn.", code="reason_required")
    if exam.plan_items.filter(plan__status=ResultPlan.Status.PUBLISHED, plan__deleted_at__isnull=True).exists():
        raise ConflictError("A published term result uses this exam. Unpublish that first.", code="used_by_plan")
    with transaction.atomic():
        exam.status, exam.published_at, exam.published_by = Exam.Status.SCHEDULED, None, None
        exam.save(update_fields=["status", "published_at", "published_by", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=exam, module=MODULE, actor=by,
            changes={"status": {"before": "published", "after": "scheduled"}}, metadata={"reason": reason.strip()})
    return exam


def refresh_results(exam: Exam, enrollment_ids: list[int], *, by=None) -> None:
    """After a correction on a published exam: recompute those students, and
    the published term results that count the exam."""
    from modules.students.models import Enrollment

    students = set(Enrollment.objects.filter(pk__in=enrollment_ids).values_list("student_id", flat=True))
    compute_exam_results(exam, only_students=students)
    for item in exam.plan_items.select_related("plan__grade_scale").filter(
            plan__status=ResultPlan.Status.PUBLISHED, plan__deleted_at__isnull=True):
        compute_plan_results(item.plan, only_students=students)
    log(AuditLog.Action.UPDATE, instance=exam, module=MODULE, actor=by,
        metadata={"recomputed_after_correction": len(students)})


# ---------------------------------------------------------------------------
# Term results: several exams by weight
# ---------------------------------------------------------------------------
def _line_outcome(line: SubjectResult) -> grading.SubjectOutcome:
    return grading.SubjectOutcome(
        line.subject_id, line.exam_subject_id, line.credit_hours, obtained=line.obtained, full=line.full,
        percentage=line.percentage, status=line.status, absent=line.absent)


def compute_plan_results(plan: ResultPlan, *, only_students=None) -> dict:
    items = list(plan.items.select_related("exam").order_by("exam__end_date", "exam__start_date", "pk"))
    if not items:
        raise ServiceError("Add the exams this term result is made from.", code="no_exams")
    scale = grading.load_scale(plan.grade_scale)
    by_exam = {}
    for item in items:
        by_exam[item.exam_id] = {r.student_id: r for r in Result.objects.filter(exam=item.exam)
                                 .select_related("enrollment", "section").prefetch_related("subjects")}
    students = set().union(*(set(v) for v in by_exam.values()))
    subject_ids = sorted({line.subject_id for exam_results in by_exam.values()
                          for result in exam_results.values() for line in result.subjects.all()})

    counts = defaultdict(int)
    with transaction.atomic():
        for student_id in students:
            if only_students is not None and student_id not in only_students:
                continue
            latest, credits = None, {}
            parts = {sid: [] for sid in subject_ids}
            for item in items:
                result = by_exam[item.exam_id].get(student_id)
                ref = {"exam": item.exam_id, "name": item.exam.name}
                lines = {l.subject_id: l for l in result.subjects.all()} if result else {}
                if result is not None:
                    latest = result
                for sid in subject_ids:
                    line = lines.get(sid)
                    if line is not None:
                        credits[sid] = line.credit_hours
                    parts[sid].append((item.weight, ref, _line_outcome(line) if line else None))
            outcomes = [grading.combine_weighted(scale, parts[sid], subject_id=sid, credit=credits.get(sid, 1))
                        for sid in subject_ids]
            overall = grading.combine(scale, outcomes)
            enrollment = latest.enrollment
            result, _ = Result.objects.get_or_create(
                plan=plan, enrollment=enrollment,
                defaults={"organization_id": plan.organization_id, "student_id": student_id,
                          "section_id": enrollment.section_id})
            Result.objects.filter(plan=plan, student_id=student_id).exclude(pk=result.pk).delete()
            _store(result, overall)
            _replace_lines(result, outcomes)
            counts[overall.status] += 1
        if only_students is None:
            Result.objects.filter(plan=plan).exclude(student_id__in=list(students)).delete()
        _rank({"plan": plan})
    return dict(counts)


def weights_total(plan: ResultPlan) -> Decimal:
    return sum((i.weight for i in plan.items.all()), Decimal("0"))


def publish_plan(plan: ResultPlan, *, by) -> dict:
    if plan.is_published:
        raise ConflictError("Already published.", code="already_published")
    items = list(plan.items.select_related("exam"))
    if not items:
        raise ServiceError("Add the exams this term result is made from.", code="no_exams")
    total = weights_total(plan)
    if total != Decimal("100"):
        raise ServiceError(f"The exam weights add up to {total}%, not 100%.", code="weights_not_100")
    unpublished = [i.exam.name for i in items if not i.exam.is_published]
    if unpublished:
        raise ConflictError("Publish these exams first: " + ", ".join(unpublished), code="exams_not_published",
                            details={"exams": unpublished})
    with transaction.atomic():
        counts = compute_plan_results(plan)
        plan.status, plan.published_at, plan.published_by = ResultPlan.Status.PUBLISHED, timezone.now(), by
        plan.save(update_fields=["status", "published_at", "published_by", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=plan, module=MODULE, actor=by,
            changes={"status": {"before": "draft", "after": "published"}}, metadata={"results": counts})
    _notify_results_published(Result.objects.filter(plan=plan), plan.name, plan.organization_id)
    return counts


def _notify_results_published(results, label: str, organization_id) -> None:
    """``ExamResultPublished``: tell each student and
    their guardians through the central Notification Service."""
    from modules.notifications.services import notify
    from modules.parents.selectors import links_for_student

    for result in results.select_related("student"):
        recipients = [result.student.user] if result.student.user_id else []
        recipients += [link.parent.user for link in links_for_student(result.student) if link.parent.user_id]
        notify(recipients, event_type="examinations.result_published", title=f"Results published: {label}",
              body=f"Your result for {label} is now available.", data={"result": result.pk},
              organization_id=organization_id)


def unpublish_plan(plan: ResultPlan, reason: str, *, by) -> ResultPlan:
    if not plan.is_published:
        raise ConflictError("It isn't published.", code="not_published")
    if not reason.strip():
        raise ServiceError("Say why the results are being withdrawn.", code="reason_required")
    plan.status, plan.published_at, plan.published_by = ResultPlan.Status.DRAFT, None, None
    plan.save(update_fields=["status", "published_at", "published_by", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=plan, module=MODULE, actor=by,
        changes={"status": {"before": "published", "after": "draft"}}, metadata={"reason": reason.strip()})
    return plan
