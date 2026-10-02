"""Alumni writes: profiles on graduation, graduating a class, RSVPs,
mentoring, and the donations ledger."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.permissions.selectors import campus_ids_with_permission
from modules.notifications.services import notify
from modules.students.models import Enrollment, Student
from modules.students.services import change_student_status

from .models import (
    LIVE_MENTORSHIP,
    ZERO,
    AlumniEvent,
    AlumniProfile,
    Campaign,
    Donation,
    DonationRefund,
    EventStatus,
    Mentorship,
    MentorshipStatus,
    Rsvp,
    RsvpResponse,
)

MODULE = "alumni"
VIEW = "alumni.view"
MANAGE = "alumni.manage"
DONATIONS = "alumni.donations"


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


def profile_for_user(user) -> AlumniProfile | None:
    if user is None or not user.is_authenticated:
        return None
    return AlumniProfile.objects.select_related("campus").filter(user=user).first()


def _next_number(organization_id: int, prefix: str, model, field: str) -> str:
    from core.organizations.models import Organization

    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=organization_id)
        # Deleted rows too, so a number is never issued twice.
        count = model.all_objects.filter(organization_id=organization_id).count()
        return f"{prefix}{count + 1:06d}"


# ---------------------------------------------------------------------------
# Graduation
# ---------------------------------------------------------------------------
def profile_for_graduate(student: Student, *, enrollment: Enrollment | None, on_date, by=None) -> AlumniProfile:
    """Run by the ``student_graduated`` signal, inside the graduation's
    transaction. Copies who they were and what they finished, and turns
    their login into an alumni login. Safe to call twice."""
    existing = AlumniProfile.all_objects.filter(student=student).first()
    if existing is not None:
        return existing
    section = getattr(enrollment, "section", None)
    profile = AlumniProfile.objects.create(
        organization_id=student.organization_id, campus_id=student.campus_id, student=student,
        user=student.user, first_name=student.first_name, middle_name=student.middle_name,
        last_name=student.last_name, gender=student.gender, date_of_birth=student.date_of_birth,
        email=student.email or (student.user.email if student.user_id else ""), phone=student.phone,
        address=student.address,
        program=section.program if section else None, program_name=section.program.name if section else "",
        level=section.level if section else None, section_name=section.name if section else "",
        academic_year=section.academic_year.name if section else "", graduated_on=on_date,
    )
    log(AuditLog.Action.CREATE, instance=profile, module=MODULE, actor=by,
        metadata={"operation": "graduation"})
    user = student.user
    if user is not None and user.user_type == user.Type.STUDENT:
        user.user_type = user.Type.ALUMNI
        user.save(update_fields=["user_type", "updated_at"])
    return profile


def graduate(students, *, on_date, reason: str = "", by=None) -> dict:
    """Graduate each student through the students module (which closes the
    enrollment and fires ``student_graduated``). One that can't graduate
    (not active, a move scheduled) is reported, not fatal to the rest."""
    done, skipped = [], []
    for student in students:
        try:
            with transaction.atomic():
                change_student_status(student=student, status=Student.Status.GRADUATED, on_date=on_date,
                                      reason=reason, by=by)
        except ServiceError as exc:
            skipped.append({"student": student.pk, "name": student.full_name, "code": exc.code,
                            "reason": str(exc.detail)})
            continue
        done.append(student.pk)
    profiles = AlumniProfile.objects.filter(student_id__in=done).order_by("pk")
    return {"graduated": len(done), "profiles": [p.pk for p in profiles], "skipped": skipped}


def students_in_section(section, on_date):
    return (Student.objects.filter(enrollments__in=Enrollment.objects.on(on_date).filter(section=section))
            .select_related("campus", "user").distinct().order_by("pk"))


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
def _lock_event(event: AlumniEvent) -> AlumniEvent:
    return AlumniEvent.objects.select_for_update().get(pk=event.pk)


def publish_event(event: AlumniEvent, *, by=None) -> AlumniEvent:
    with transaction.atomic():
        event = _lock_event(event)
        if event.status != EventStatus.DRAFT:
            raise ConflictError("Only a draft can be published.", code="not_draft")
        if event.starts_at <= timezone.now():
            raise ConflictError("This event has already started.", code="started")
        event.status = EventStatus.PUBLISHED
        event.save(update_fields=["status", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=event, module=MODULE, actor=by,
            changes={"status": {"before": EventStatus.DRAFT, "after": EventStatus.PUBLISHED}})
    return event


def cancel_event(event: AlumniEvent, *, reason: str, by=None) -> AlumniEvent:
    if not reason.strip():
        raise ServiceError("Say why it's cancelled.", code="reason_required")
    with transaction.atomic():
        event = _lock_event(event)
        if event.status == EventStatus.CANCELLED:
            raise ConflictError("Already cancelled.", code="cancelled")
        before = event.status
        event.status = EventStatus.CANCELLED
        event.save(update_fields=["status", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=event, module=MODULE, actor=by,
            changes={"status": {"before": before, "after": EventStatus.CANCELLED}}, metadata={"reason": reason})
    people = [r.profile.user for r in event.rsvps.select_related("profile__user")
              .exclude(response=RsvpResponse.DECLINED)]
    notify(people, event_type="alumni.event_cancelled", title=f"Cancelled: {event.title}", body=reason.strip(),
           data={"alumni_event": event.pk}, organization_id=event.organization_id)
    return event


def places_taken(event: AlumniEvent, exclude_profile=None) -> int:
    qs = event.rsvps.filter(response=RsvpResponse.GOING)
    if exclude_profile is not None:
        qs = qs.exclude(profile=exclude_profile)
    going = qs.aggregate(people=Sum("guests"))["people"] or 0
    return going + qs.count()


def event_open_to(event: AlumniEvent, profile: AlumniProfile) -> bool:
    return (event.organization_id == profile.organization_id and event.status == EventStatus.PUBLISHED
            and (event.campus_id is None or event.campus_id == profile.campus_id))


def rsvp(event: AlumniEvent, profile: AlumniProfile, *, response: str, guests: int = 0) -> Rsvp:
    with transaction.atomic():
        event = _lock_event(event)
        if not event_open_to(event, profile):
            raise ConflictError("This event isn't open to you.", code="not_open")
        if event.starts_at <= timezone.now():
            raise ConflictError("This event has already started.", code="started")
        if response != RsvpResponse.GOING:
            guests = 0
        if response == RsvpResponse.GOING and event.capacity is not None:
            if places_taken(event, exclude_profile=profile) + 1 + guests > event.capacity:
                raise ConflictError("Not enough places left.", code="full")
        row = Rsvp.objects.filter(event=event, profile=profile).first()
        if row is None:
            row = Rsvp.objects.create(organization_id=event.organization_id, event=event, profile=profile,
                                      response=response, guests=guests)
        else:
            row.response, row.guests = response, guests
            row.save(update_fields=["response", "guests", "updated_at"])
    return row


# ---------------------------------------------------------------------------
# Mentoring
# ---------------------------------------------------------------------------
def request_mentorship(*, mentor: AlumniProfile, topic: str, message: str = "", student=None,
                       mentee: AlumniProfile | None = None) -> Mentorship:
    if not mentor.is_mentor:
        raise ConflictError("This person isn't taking mentees.", code="not_a_mentor")
    if mentor.user_id is None:
        raise ConflictError("This mentor has no account to answer from yet.", code="mentor_unreachable")
    if mentee is not None and mentee.pk == mentor.pk:
        raise ServiceError("You can't mentor yourself.", code="self")
    if student is not None and mentor.student_id == student.pk:
        raise ServiceError("You can't mentor yourself.", code="self")
    asking = Mentorship.objects.filter(mentor=mentor, status__in=LIVE_MENTORSHIP)
    asking = asking.filter(student=student) if student is not None else asking.filter(mentee=mentee)
    if asking.exists():
        raise ConflictError("You already have a request or a mentorship with this mentor.", code="duplicate")
    with transaction.atomic():
        row = Mentorship.objects.create(organization_id=mentor.organization_id, mentor=mentor, student=student,
                                        mentee=mentee, topic=topic, message=message)
    name = student.full_name if student is not None else mentee.full_name
    notify([mentor.user], event_type="alumni.mentorship_requested", title=f"Mentoring request from {name}",
           body=topic, data={"mentorship": row.pk}, organization_id=mentor.organization_id)
    return row


def mentee_users(row: Mentorship):
    if row.student_id:
        return [row.student.user]
    return [row.mentee.user]


def respond_mentorship(row: Mentorship, *, accept: bool, note: str = "") -> Mentorship:
    with transaction.atomic():
        # Lock the mentor, so two acceptances can't both take the last place.
        mentor = AlumniProfile.objects.select_for_update().get(pk=row.mentor_id)
        row = Mentorship.objects.select_for_update().get(pk=row.pk)
        if row.status != MentorshipStatus.PENDING:
            raise ConflictError("This request has already been answered.", code="not_pending")
        if accept:
            active = Mentorship.objects.filter(mentor=mentor, status=MentorshipStatus.ACCEPTED).count()
            if active >= mentor.mentor_capacity:
                raise ConflictError("You're at your mentee limit; end one or raise your capacity first.",
                                    code="at_capacity")
        row.status = MentorshipStatus.ACCEPTED if accept else MentorshipStatus.DECLINED
        row.responded_at, row.note = timezone.now(), note
        row.save(update_fields=["status", "responded_at", "note", "updated_at"])
    verb = "accepted" if accept else "declined"
    notify(mentee_users(row), event_type=f"alumni.mentorship_{verb}",
           title=f"{row.mentor.full_name} {verb} your mentoring request", body=note,
           data={"mentorship": row.pk}, organization_id=row.organization_id)
    return row


def close_mentorship(row: Mentorship, *, by_mentee: bool, note: str = "") -> Mentorship:
    """The mentee withdraws a request still pending; either side ends an
    accepted one."""
    with transaction.atomic():
        row = Mentorship.objects.select_for_update().get(pk=row.pk)
        if row.status == MentorshipStatus.PENDING and by_mentee:
            row.status = MentorshipStatus.CANCELLED
        elif row.status == MentorshipStatus.ACCEPTED:
            row.status = MentorshipStatus.ENDED
        else:
            raise ConflictError("Nothing to end here.", code="not_open")
        row.ended_at, row.note = timezone.now(), note or row.note
        row.save(update_fields=["status", "ended_at", "note", "updated_at"])
    return row


# ---------------------------------------------------------------------------
# Donations
# ---------------------------------------------------------------------------
def _refresh_campaign(campaign_id) -> None:
    if campaign_id is None:
        return
    campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
    totals = Donation.objects.filter(campaign=campaign).aggregate(given=Sum("amount"), back=Sum("refunded_amount"))
    campaign.raised_amount = (totals["given"] or ZERO) - (totals["back"] or ZERO)
    campaign.save(update_fields=["raised_amount", "updated_at"])


def record_donation(*, campus, amount: Decimal, received_on, donor_name: str = "", donor=None, campaign=None,
                    by=None, **fields) -> Donation:
    if campaign is not None:
        if campaign.campus_id is not None and campaign.campus_id != campus.pk:
            raise ServiceError("This campaign belongs to another campus.", code="wrong_campus")
        if not campaign.is_active:
            raise ConflictError("This campaign is closed.", code="campaign_closed")
    if not donor_name and donor is None:
        raise ServiceError("Name the donor.", code="donor_required")
    with transaction.atomic():
        donation = Donation.objects.create(
            organization_id=campus.organization_id, campus=campus, campaign=campaign, donor=donor,
            donor_name=donor_name or donor.full_name,
            donor_email=fields.pop("donor_email", "") or (donor.email if donor else ""),
            donor_phone=fields.pop("donor_phone", "") or (donor.phone if donor else ""),
            amount=amount, received_on=received_on,
            receipt_number=_next_number(campus.organization_id, "DON-", Donation, "receipt_number"),
            recorded_by=by, **fields)
        _refresh_campaign(donation.campaign_id)
        log(AuditLog.Action.CREATE, instance=donation, module=MODULE, actor=by)
    if donor is not None:
        notify([donor.user], event_type="alumni.donation_received", title=f"Thank you: {donation.receipt_number}",
               body=f"We received your gift of {donation.amount}.", data={"donation": donation.pk},
               organization_id=donation.organization_id)
    return donation


def refund_donation(donation: Donation, *, amount: Decimal, reason: str, refunded_on=None, by=None,
                    **fields) -> DonationRefund:
    if not reason.strip():
        raise ServiceError("Say why it's refunded.", code="reason_required")
    refunded_on = refunded_on or timezone.localdate()
    with transaction.atomic():
        donation = Donation.objects.select_for_update().get(pk=donation.pk)
        if refunded_on < donation.received_on:
            raise ServiceError("A refund can't be dated before the gift.", code="invalid_date")
        left = donation.amount - donation.refunded_amount
        if amount > left:
            raise ConflictError(f"Only {left} is left to refund on this gift.", code="over_refund")
        refund = DonationRefund.objects.create(organization_id=donation.organization_id, donation=donation,
                                               amount=amount, refunded_on=refunded_on, reason=reason.strip(),
                                               recorded_by=by, **fields)
        donation.refunded_amount += amount
        donation.save(update_fields=["refunded_amount", "updated_at"])
        _refresh_campaign(donation.campaign_id)
        log(AuditLog.Action.CREATE, instance=refund, module=MODULE, actor=by,
            metadata={"donation": donation.receipt_number})
    return refund


def ensure_scope(user, code: str, campus_id) -> None:
    if not holds(user, code, campus_id):
        raise PermissionDeniedError("Your role does not cover this campus for this action.", code="wrong_campus")
