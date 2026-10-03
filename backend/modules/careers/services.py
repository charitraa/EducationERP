"""Careers writes: vacancies, applying, screening, interviews, offers,
hiring, and the job board.

Applying opens an application (kind ``job``) through the
applications service, so the approval chain, the history, sending back
and withdrawing all work as for any other application. Hiring is that
application's final approval: ``hire`` runs inside it, so a refusal (no
accepted offer, the vacancy already filled, the employee number taken)
refuses the approval and nothing is created.
"""
from django.db import transaction
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.files import services as files
from core.permissions.selectors import campus_ids_with_permission, users_holding
from modules.applications import services as applications
from modules.applications.models import OPEN, Status
from modules.notifications.services import notify, notify_address
from modules.staff.selectors import staff_member_for_user

from .models import (
    LIVE_OFFER,
    Candidacy,
    Interview,
    InterviewStatus,
    JobOffer,
    JobPosting,
    OfferStatus,
    PostingStatus,
    Vacancy,
    VacancyStatus,
)

MODULE = "careers"
VIEW = "careers.view"
MANAGE = "careers.manage"
HIRE = "careers.hire"
BOARD = "careers.board"
CANDIDACY = "careers.candidacy"  # owner type of an attached résumé


def holds(user, code: str, campus_id) -> bool:
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


def holds_anywhere(user, code: str) -> bool:
    if user is None or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or bool(campus_ids)


def ensure(user, code: str, campus_id) -> None:
    if not holds(user, code, campus_id):
        raise PermissionDeniedError("Your role does not cover this campus for this action.", code="wrong_campus")


def tell_candidate(candidacy: Candidacy, title: str, body: str = "") -> None:
    application = candidacy.application
    if application.applicant_id:
        notify([application.applicant], event_type="careers.candidate", title=title, body=body,
               data={"application": application.pk}, organization_id=candidacy.organization_id)
    else:
        notify_address(email=candidacy.email, phone=candidacy.phone, title=f"{title} ({application.number})",
                       body=body)


# ---------------------------------------------------------------------------
# Vacancies
# ---------------------------------------------------------------------------
def check_type(vacancy: Vacancy) -> None:
    kind_type = vacancy.application_type
    if kind_type.kind != "job":
        raise ServiceError("Pick a job application form.", code="not_a_job_form")
    if not kind_type.is_active or not kind_type.steps.exists():
        raise ConflictError("That job form is inactive or has no approval steps.", code="form_not_ready")
    if kind_type.campus_id and kind_type.campus_id != vacancy.campus_id:
        raise ServiceError("That job form is for another campus.", code="wrong_campus")
    if vacancy.is_public and not kind_type.is_public:
        raise ServiceError("A public vacancy needs a public job form.", code="form_not_public")


def open_vacancy(vacancy: Vacancy, *, by=None) -> Vacancy:
    with transaction.atomic():
        vacancy = Vacancy.objects.select_for_update().select_related("application_type").get(pk=vacancy.pk)
        if vacancy.status not in (VacancyStatus.DRAFT, VacancyStatus.CLOSED):
            raise ConflictError(f"This vacancy is {vacancy.get_status_display().lower()}.", code="not_openable")
        if vacancy.hired_count >= vacancy.openings:
            raise ConflictError("Every opening is filled; add openings first.", code="filled")
        check_type(vacancy)
        today = timezone.localdate()
        if vacancy.closes_on is not None and vacancy.closes_on < today:
            raise ServiceError("The closing date has passed; move it first.", code="closes_in_past")
        before = vacancy.status
        vacancy.status = VacancyStatus.OPEN
        vacancy.opens_on = vacancy.opens_on or today
        vacancy.save(update_fields=["status", "opens_on", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=vacancy, module=MODULE, actor=by,
            changes={"status": {"before": before, "after": VacancyStatus.OPEN}})
    return vacancy


def close_vacancy(vacancy: Vacancy, *, by=None) -> Vacancy:
    with transaction.atomic():
        vacancy = Vacancy.objects.select_for_update().get(pk=vacancy.pk)
        if vacancy.status != VacancyStatus.OPEN:
            raise ConflictError("Only an open vacancy can be closed.", code="not_open")
        vacancy.status = VacancyStatus.CLOSED
        vacancy.save(update_fields=["status", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=vacancy, module=MODULE, actor=by,
            changes={"status": {"before": VacancyStatus.OPEN, "after": VacancyStatus.CLOSED}})
    return vacancy


def is_accepting(vacancy: Vacancy) -> bool:
    today = timezone.localdate()
    return (vacancy.status == VacancyStatus.OPEN and (vacancy.opens_on is None or vacancy.opens_on <= today)
            and (vacancy.closes_on is None or vacancy.closes_on >= today))


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------
def apply(vacancy: Vacancy, *, raw_data, by=None, resume_file=None, upload=None,
          public: bool = False) -> tuple:
    """Open a candidacy: a ``job`` application through the applications
    engine plus the link to this vacancy and the résumé. ``resume_file`` is
    a file the signed-in applicant uploaded; ``upload`` comes with a public
    form. Returns (candidacy, token); the token is for a public applicant."""
    if not is_accepting(vacancy):
        raise ConflictError("This vacancy isn't taking applications.", code="not_open")
    if public and not vacancy.is_public:
        raise ConflictError("This vacancy is for signed-in applicants.", code="not_public")
    if by is not None and by.is_authenticated and staff_member_for_user(by) is not None:
        raise ConflictError("You already work here; moves between posts go through HR.", code="already_staff")
    if upload is not None:
        files.check(upload, "resume")
    if vacancy.resume_required and resume_file is None and upload is None:
        raise ServiceError("Attach a résumé.", code="resume_required")
    if resume_file is not None and (resume_file.organization_id != vacancy.organization_id
                                    or resume_file.purpose != "resume"):
        raise ServiceError("Upload the résumé with purpose 'resume' first.", code="wrong_purpose")

    application_type = vacancy.application_type
    data = raw_data if isinstance(raw_data, dict) else {}
    email = str(data.get("email") or "").strip().lower()

    stored = None
    try:
        with transaction.atomic():
            # One applicant at a time per vacancy, so two identical applications can't both pass the check.
            Vacancy.objects.select_for_update().get(pk=vacancy.pk)
            open_here = Candidacy.objects.filter(vacancy=vacancy, application__status__in=OPEN)
            if email and open_here.filter(email__iexact=email).exists():
                raise ConflictError("An application with this email is already open for this vacancy.",
                                    code="duplicate")
            if by is not None and by.is_authenticated and open_here.filter(application__applicant=by).exists():
                raise ConflictError("You already have an open application for this vacancy.", code="duplicate")
            name = " ".join(p for p in (data.get("first_name"), data.get("last_name")) if p)
            application, token = applications.submit(
                application_type=application_type, campus=vacancy.campus, raw_data=raw_data, by=by,
                contact={"name": name[:200], "email": email, "phone": str(data.get("phone") or "")[:32]},
                public=public)
            clean = application.data
            candidacy = Candidacy.objects.create(
                organization_id=vacancy.organization_id, vacancy=vacancy, application=application,
                full_name=" ".join(p for p in (clean["first_name"], clean.get("middle_name"), clean["last_name"])
                                   if p)[:200],
                email=clean.get("email", ""), phone=clean.get("phone", ""))
            if upload is not None:
                stored = files.store(upload, organization_id=vacancy.organization_id, purpose="resume")
                resume_file = stored
                files.attach(stored, owner_type=CANDIDACY, owner_id=candidacy.pk, purpose="resume")
            elif resume_file is not None:
                files.attach(resume_file, owner_type=CANDIDACY, owner_id=candidacy.pk, purpose="resume", by=by)
            if resume_file is not None:
                candidacy.resume = resume_file
                candidacy.save(update_fields=["resume", "updated_at"])
            log(AuditLog.Action.CREATE, instance=candidacy, module=MODULE, actor=application.applicant)
    except Exception:
        if stored is not None:
            stored.file.delete(save=False)  # the row rolled back; don't leave the bytes behind
        raise
    return candidacy, token


def can_see_candidacy(user, candidacy: Candidacy) -> bool:
    """The office, an interviewer on its panel, whoever decides its step, or
    the applicant."""
    if holds(user, VIEW, candidacy.vacancy.campus_id):
        return True
    staff = staff_member_for_user(user)
    if staff is not None and candidacy.interviews.filter(panel=staff).exists():
        return True
    application = candidacy.application
    return applications.is_party(user, application) or applications.can_decide(user, application)


def can_read_resume(user, candidacy_id) -> bool:
    candidacy = (Candidacy.objects.select_related("vacancy", "application__application_type", "application__student",
                                                   "application__staff").filter(pk=candidacy_id).first())
    return candidacy is not None and can_see_candidacy(user, candidacy)


def screen(candidacy: Candidacy, *, score: int | None, note: str, by=None) -> Candidacy:
    if candidacy.application.status not in OPEN:
        raise ConflictError("This application is closed.", code="closed")
    candidacy.screening_score, candidacy.screening_note = score, note
    candidacy.save(update_fields=["screening_score", "screening_note", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=candidacy, module=MODULE, actor=by,
        changes={"screening_score": {"before": None, "after": score}})
    return candidacy


# ---------------------------------------------------------------------------
# Interviews
# ---------------------------------------------------------------------------
def _ensure_in_review(candidacy: Candidacy) -> None:
    if candidacy.application.status != Status.IN_REVIEW:
        raise ConflictError("This application isn't in review.", code="not_in_review")


def _when(interview: Interview) -> str:
    return timezone.localtime(interview.scheduled_at).strftime("%Y-%m-%d %H:%M")


def schedule_interview(candidacy: Candidacy, *, scheduled_at, panel=(), by=None, **fields) -> Interview:
    _ensure_in_review(candidacy)
    if scheduled_at <= timezone.now():
        raise ServiceError("Schedule it in the future.", code="in_past")
    with transaction.atomic():
        interview = Interview.objects.create(organization_id=candidacy.organization_id, candidacy=candidacy,
                                             scheduled_at=scheduled_at, **fields)
        interview.panel.set(panel)
        log(AuditLog.Action.CREATE, instance=interview, module=MODULE, actor=by)
    where = interview.location or interview.get_mode_display()
    tell_candidate(candidacy, f"Interview for {candidacy.vacancy.title}", f"{_when(interview)}, {where}.")
    notify([s.user for s in panel], event_type="careers.interview_panel",
           title=f"Interview: {candidacy.full_name} for {candidacy.vacancy.title}", body=f"{_when(interview)}, {where}.",
           data={"interview": interview.pk}, organization_id=candidacy.organization_id)
    return interview


def reschedule_interview(interview: Interview, *, scheduled_at, location=None, by=None) -> Interview:
    if interview.status != InterviewStatus.SCHEDULED:
        raise ConflictError("Only a scheduled interview can move.", code="not_scheduled")
    _ensure_in_review(interview.candidacy)
    if scheduled_at <= timezone.now():
        raise ServiceError("Schedule it in the future.", code="in_past")
    before = interview.scheduled_at
    interview.scheduled_at = scheduled_at
    if location is not None:
        interview.location = location
    interview.save(update_fields=["scheduled_at", "location", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=interview, module=MODULE, actor=by,
        changes={"scheduled_at": {"before": before.isoformat(), "after": scheduled_at.isoformat()}})
    candidacy = interview.candidacy
    tell_candidate(candidacy, f"Interview moved: {candidacy.vacancy.title}",
                   f"Now {_when(interview)}, {interview.location or interview.get_mode_display()}.")
    return interview


def is_panelist(user, interview: Interview) -> bool:
    staff = staff_member_for_user(user)
    return staff is not None and interview.panel.filter(pk=staff.pk).exists()


def record_outcome(interview: Interview, *, status: str, by, score=None, recommendation: str = "",
                   feedback: str = "") -> Interview:
    if not (is_panelist(by, interview) or holds(by, MANAGE, interview.candidacy.vacancy.campus_id)):
        raise PermissionDeniedError("Only the panel or the office can record this.", code="not_panel")
    with transaction.atomic():
        interview = Interview.objects.select_for_update().get(pk=interview.pk)
        if interview.status != InterviewStatus.SCHEDULED:
            raise ConflictError("This interview is already closed.", code="not_scheduled")
        if interview.scheduled_at > timezone.now():
            raise ConflictError("This interview hasn't happened yet.", code="not_started")
        interview.status = status
        interview.score, interview.recommendation, interview.feedback = score, recommendation, feedback
        interview.save(update_fields=["status", "score", "recommendation", "feedback", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=interview, module=MODULE, actor=by,
            changes={"status": {"before": InterviewStatus.SCHEDULED, "after": status}})
    return interview


def cancel_interview(interview: Interview, *, reason: str, by=None) -> Interview:
    with transaction.atomic():
        interview = Interview.objects.select_for_update().get(pk=interview.pk)
        if interview.status != InterviewStatus.SCHEDULED:
            raise ConflictError("This interview is already closed.", code="not_scheduled")
        interview.status = InterviewStatus.CANCELLED
        interview.feedback = reason
        interview.save(update_fields=["status", "feedback", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=interview, module=MODULE, actor=by,
            changes={"status": {"before": InterviewStatus.SCHEDULED, "after": InterviewStatus.CANCELLED}})
    tell_candidate(interview.candidacy, f"Interview cancelled: {interview.candidacy.vacancy.title}", reason)
    return interview


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------
def _at_last_step(application) -> bool:
    return not application.application_type.steps.filter(sequence__gt=application.step).exists()


def make_offer(candidacy: Candidacy, *, start_date, by, **fields) -> JobOffer:
    ensure(by, HIRE, candidacy.vacancy.campus_id)
    today = timezone.localdate()
    if start_date < today:
        raise ServiceError("The start date has passed.", code="in_past")
    if fields.get("expires_on") is not None and fields["expires_on"] < today:
        raise ServiceError("The offer would already have expired.", code="in_past")
    with transaction.atomic():
        candidacy = Candidacy.objects.select_for_update().select_related(
            "vacancy", "application__application_type").get(pk=candidacy.pk)
        _ensure_in_review(candidacy)
        if not _at_last_step(candidacy.application):
            raise ConflictError("Make an offer once the candidate reaches the last step.", code="not_last_step")
        if candidacy.offers.filter(status__in=LIVE_OFFER).exists():
            raise ConflictError("There's already a live offer; withdraw it first.", code="live_offer")
        offer = JobOffer.objects.create(organization_id=candidacy.organization_id, candidacy=candidacy,
                                        start_date=start_date, made_by=by, **fields)
        log(AuditLog.Action.CREATE, instance=offer, module=MODULE, actor=by)
    until = f" Please reply by {offer.expires_on}." if offer.expires_on else ""
    tell_candidate(candidacy, f"Job offer: {candidacy.vacancy.title}", f"Starting {offer.start_date}.{until}")
    return offer


def respond_offer(offer: JobOffer, *, accept: bool, note: str = "") -> JobOffer:
    with transaction.atomic():
        offer = JobOffer.objects.select_for_update().select_related("candidacy__application",
                                                                    "candidacy__vacancy").get(pk=offer.pk)
        if offer.status != OfferStatus.MADE:
            raise ConflictError("This offer has already been answered or withdrawn.", code="not_open")
        _ensure_in_review(offer.candidacy)
        if accept and offer.expires_on is not None and offer.expires_on < timezone.localdate():
            raise ConflictError("This offer has expired.", code="expired")
        offer.status = OfferStatus.ACCEPTED if accept else OfferStatus.DECLINED
        offer.responded_at, offer.response_note = timezone.now(), note
        offer.save(update_fields=["status", "responded_at", "response_note", "updated_at"])
    candidacy = offer.candidacy
    verb = "accepted" if accept else "declined"
    notify(users_holding([HIRE], campus_id=candidacy.vacancy.campus_id, organization_id=offer.organization_id),
           event_type=f"careers.offer_{verb}", title=f"{candidacy.full_name} {verb} the offer",
           body=f"{candidacy.vacancy.title}. {note}".strip(), data={"offer": offer.pk},
           organization_id=offer.organization_id)
    return offer


def withdraw_offer(offer: JobOffer, *, reason: str, by) -> JobOffer:
    ensure(by, HIRE, offer.candidacy.vacancy.campus_id)
    with transaction.atomic():
        offer = JobOffer.objects.select_for_update().get(pk=offer.pk)
        if offer.status not in LIVE_OFFER:
            raise ConflictError("This offer isn't live.", code="not_open")
        before = offer.status
        offer.status, offer.response_note = OfferStatus.WITHDRAWN, reason
        offer.save(update_fields=["status", "response_note", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=offer, module=MODULE, actor=by,
            changes={"status": {"before": before, "after": OfferStatus.WITHDRAWN}})
    tell_candidate(offer.candidacy, f"Offer withdrawn: {offer.candidacy.vacancy.title}", reason)
    return offer


# ---------------------------------------------------------------------------
# Hiring (the job application's final approval)
# ---------------------------------------------------------------------------
def hire(application, decision: dict, by) -> tuple[dict, str]:
    from modules.hr.services import start_contract
    from modules.staff.services import create_staff_member

    candidacy = Candidacy.objects.select_for_update().select_related("vacancy__campus").filter(
        application=application).first()
    if candidacy is None:
        raise ConflictError("This application isn't for a vacancy.", code="no_vacancy")
    offer = candidacy.offers.filter(status=OfferStatus.ACCEPTED).first()
    if offer is None:
        raise ConflictError("Hire once the candidate has accepted an offer.", code="no_accepted_offer")
    vacancy = Vacancy.objects.select_for_update().get(pk=candidacy.vacancy_id)
    if vacancy.hired_count >= vacancy.openings:
        raise ConflictError("Every opening for this vacancy is filled.", code="vacancy_filled")

    data = application.data
    user = application.applicant
    link = user is not None and user.user_type in (user.Type.ALUMNI, user.Type.STAFF, user.Type.TEACHER)
    staff = create_staff_member(
        campus=vacancy.campus, employee_number=decision["employee_number"], first_name=data["first_name"],
        middle_name=data.get("middle_name", ""), last_name=data["last_name"], by=by, user=user if link else None,
        date_of_birth=data.get("date_of_birth") or None, gender=data.get("gender", ""),
        email=data.get("email", ""), phone=data.get("phone", ""), address=data.get("address", ""),
        staff_type=vacancy.staff_type, designation=vacancy.title[:100], joined_on=offer.start_date)
    contract = start_contract(staff=staff, kind=offer.contract_kind, start_date=offer.start_date,
                              end_date=offer.contract_end_date, position=vacancy.position,
                              department=vacancy.department, probation_ends_on=offer.probation_ends_on,
                              reference=application.number, notes=offer.terms, by=by)
    if link and user.user_type == user.Type.ALUMNI:
        user.user_type = user.Type.TEACHER if vacancy.staff_type == "teaching" else user.Type.STAFF
        user.save(update_fields=["user_type", "updated_at"])
    candidacy.hired_staff = staff
    candidacy.save(update_fields=["hired_staff", "updated_at"])
    vacancy.hired_count += 1
    fields = ["hired_count", "updated_at"]
    if vacancy.hired_count >= vacancy.openings:
        vacancy.status = VacancyStatus.FILLED
        fields.append("status")
    vacancy.save(update_fields=fields)
    return ({"type": "staff.staff_member", "id": staff.pk, "contract": contract.pk},
            f"Hired as {staff.employee_number}, starting {offer.start_date}.")


# ---------------------------------------------------------------------------
# Job board
# ---------------------------------------------------------------------------
def post_job(*, by, can_moderate: bool, campus_id=None, **fields) -> JobPosting:
    """``campus_id``: the poster's campus (an alumnus's), whose moderators
    are asked to approve; organization-wide moderators always are."""
    status = PostingStatus.APPROVED if can_moderate else PostingStatus.PENDING
    with transaction.atomic():
        posting = JobPosting.objects.create(organization_id=by.organization_id, posted_by=by, status=status,
                                            reviewed_by=by if can_moderate else None,
                                            reviewed_at=timezone.now() if can_moderate else None, **fields)
        log(AuditLog.Action.CREATE, instance=posting, module=MODULE, actor=by)
    if not can_moderate:
        notify(users_holding([BOARD], campus_id=campus_id, organization_id=by.organization_id),
               event_type="careers.posting_submitted", title=f"Job board: {posting.title} at {posting.company}",
               body="Waiting for approval.", data={"posting": posting.pk}, organization_id=by.organization_id)
    return posting


def review_posting(posting: JobPosting, *, approve: bool, note: str = "", by) -> JobPosting:
    with transaction.atomic():
        posting = JobPosting.objects.select_for_update().get(pk=posting.pk)
        if posting.status != PostingStatus.PENDING:
            raise ConflictError("This posting has already been reviewed.", code="not_pending")
        if not approve and not note.strip():
            raise ServiceError("Say why it isn't approved.", code="reason_required")
        posting.status = PostingStatus.APPROVED if approve else PostingStatus.REJECTED
        posting.reviewed_by, posting.reviewed_at, posting.review_note = by, timezone.now(), note.strip()
        posting.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=posting, module=MODULE, actor=by,
            changes={"status": {"before": PostingStatus.PENDING, "after": posting.status}})
    verb = "approved" if approve else "not approved"
    notify([posting.posted_by], event_type="careers.posting_reviewed", title=f"Your job posting was {verb}",
           body=note.strip() or posting.title, data={"posting": posting.pk}, organization_id=posting.organization_id)
    return posting


def close_posting(posting: JobPosting, *, by) -> JobPosting:
    if posting.status not in (PostingStatus.PENDING, PostingStatus.APPROVED):
        raise ConflictError("This posting is already closed.", code="not_open")
    before = posting.status
    posting.status = PostingStatus.CLOSED
    posting.save(update_fields=["status", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=posting, module=MODULE, actor=by,
        changes={"status": {"before": before, "after": PostingStatus.CLOSED}})
    return posting
