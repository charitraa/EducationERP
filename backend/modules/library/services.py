"""Membership, circulation (issue/return/lost), reservations and fines.

Views and serializers call these so a rule lives in one place, matching
every other module.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.permissions.selectors import campus_ids_with_permission

from .models import (
    Copy,
    CopyStatus,
    Fine,
    FineCategory,
    FineStatus,
    Issue,
    IssueStatus,
    Member,
    MEMBERSHIP_DEFAULTS,
    MembershipType,
    Reservation,
    ReservationStatus,
)

MODULE = "library"
MANAGE = "library.manage"
CIRCULATE = "library.circulate"

RESERVATION_HOLD_DAYS = 3


def _next_number(organization_id: int, prefix: str, model) -> str:
    from core.organizations.models import Organization

    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=organization_id)
        # Deleted rows too, or a number freed by a delete would be issued again
        # while a later row still holds it.
        count = model.all_objects.filter(organization_id=organization_id).count()
        return f"{prefix}{count + 1:06d}"


def holds(user, code: str, campus_id) -> bool:
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


def ensure_can_circulate(user, campus_id) -> None:
    if not holds(user, CIRCULATE, campus_id):
        raise PermissionDeniedError("Only the library desk can do this.", code="not_library_desk")


def ensure_self_or_circulate(user, member: Member) -> None:
    """A member reserving (or cancelling) their own hold, or the library
    desk acting for them — the same "own record, or the office" shape as
    ``communication.ensure_can_manage_appointment``."""
    if member.user is not None and member.user.pk == user.pk:
        return
    if holds(user, CIRCULATE, member.campus_id):
        return
    raise PermissionDeniedError("Only this member, or the library desk, can do this.", code="not_yours")


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------
def create_member(*, campus, student=None, staff=None, by=None, **overrides) -> Member:
    if (student is None) == (staff is None):
        raise ServiceError("A membership belongs to exactly one student or one staff member.",
                           code="bad_profile")
    membership_type = MembershipType.STUDENT if student is not None else MembershipType.STAFF
    organization_id = (student or staff).organization_id
    if Member.objects.filter(organization_id=organization_id, student=student, staff=staff).exists():
        raise ConflictError("This person is already a library member.", code="already_member")
    defaults = {**MEMBERSHIP_DEFAULTS[membership_type], **overrides}
    with transaction.atomic():
        member = Member.objects.create(
            organization_id=organization_id, campus=campus, student=student, staff=staff,
            membership_type=membership_type, member_number=_next_number(organization_id, "LM-", Member),
            joined_on=overrides.get("joined_on") or timezone.localdate(), **{
                k: v for k, v in defaults.items() if k != "joined_on"},
        )
        log(AuditLog.Action.CREATE, instance=member, module=MODULE, actor=by)
    return member


def deactivate_member(member: Member, *, by=None) -> Member:
    if member.issues.filter(status=IssueStatus.ISSUED).exists():
        raise ConflictError("This member still has books out.", code="books_out")
    member.is_active = False
    member.save(update_fields=["is_active", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=member, module=MODULE, actor=by,
        changes={"is_active": {"before": True, "after": False}})
    return member


# ---------------------------------------------------------------------------
# Circulation
# ---------------------------------------------------------------------------
def issue_book(*, copy: Copy, member: Member, by=None, _expect: str = CopyStatus.AVAILABLE) -> Issue:
    if not member.is_active:
        raise ConflictError("This membership isn't active.", code="inactive_member")
    if copy.campus_id != member.campus_id:
        raise ServiceError("This copy belongs to a different campus's library.", code="wrong_campus")
    if member.issues.filter(status=IssueStatus.ISSUED).count() >= member.max_books:
        raise ConflictError(f"{member.full_name} already has {member.max_books} books out.",
                            code="limit_reached")
    with transaction.atomic():
        copy = Copy.objects.select_for_update().get(pk=copy.pk)
        if copy.status != _expect:
            raise ConflictError("This copy isn't available.", code="not_available")
        now = timezone.now()
        issue = Issue.objects.create(
            organization_id=copy.organization_id, copy=copy, member=member, issued_at=now,
            due_at=now + timedelta(days=member.loan_period_days), issued_by=by,
        )
        copy.status = CopyStatus.ISSUED
        copy.save(update_fields=["status", "updated_at"])
        log(AuditLog.Action.CREATE, instance=issue, module=MODULE, actor=by,
            metadata={"copy": copy.accession_number, "member": member.member_number})
    return issue


def _overdue_fine(issue: Issue, *, by=None) -> Fine | None:
    if issue.returned_at is None or issue.returned_at <= issue.due_at:
        return None
    days = (issue.returned_at.date() - issue.due_at.date()).days
    if days <= 0:
        return None
    return Fine.objects.create(
        organization_id=issue.organization_id, member=issue.member, issue=issue, category=FineCategory.OVERDUE,
        amount=issue.member.daily_fine_rate * days, note=f"{days} day(s) overdue",
    )


def _release_copy(copy: Copy, *, by=None) -> None:
    """A copy just freed up: hold it for the oldest pending reservation, or
    make it available to anyone."""
    reservation = (copy.book.reservations.filter(status=ReservationStatus.PENDING)
                  .order_by("reserved_at").first())
    if reservation is None:
        copy.status = CopyStatus.AVAILABLE
        copy.save(update_fields=["status", "updated_at"])
        return
    now = timezone.now()
    reservation.status = ReservationStatus.READY
    reservation.ready_at = now
    reservation.expires_at = now + timedelta(days=RESERVATION_HOLD_DAYS)
    reservation.copy = copy
    reservation.save(update_fields=["status", "ready_at", "expires_at", "copy", "updated_at"])
    copy.status = CopyStatus.RESERVED
    copy.save(update_fields=["status", "updated_at"])
    _notify_reservation_ready(reservation)


def return_book(*, issue: Issue, outcome: str = "returned", by=None) -> Issue:
    """``outcome`` is ``returned``, ``damaged`` or ``lost``. Either of the
    last two also raises a fine for the copy's replacement cost, on top of
    any overdue fine already run up."""
    if issue.status != IssueStatus.ISSUED:
        raise ConflictError("This copy isn't out on loan.", code="not_issued")
    if outcome not in ("returned", "damaged", "lost"):
        raise ServiceError("Unknown outcome.", code="bad_outcome")
    if outcome in ("damaged", "lost") and issue.copy.price is None:
        raise ServiceError("Set the copy's price before reporting it damaged or lost.", code="no_price")
    with transaction.atomic():
        now = timezone.now()
        issue.returned_at = now
        issue.returned_to = by
        issue.status = IssueStatus.RETURNED if outcome == "returned" else IssueStatus.LOST
        issue.save(update_fields=["returned_at", "returned_to", "status", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=issue, module=MODULE, actor=by,
            changes={"status": {"before": "issued", "after": issue.status}}, metadata={"outcome": outcome})

        copy = issue.copy
        _overdue_fine(issue, by=by)
        if outcome == "returned":
            _release_copy(copy, by=by)
        else:
            copy.status = CopyStatus.DAMAGED if outcome == "damaged" else CopyStatus.LOST
            copy.save(update_fields=["status", "updated_at"])
            Fine.objects.create(
                organization_id=issue.organization_id, member=issue.member, issue=issue,
                category=FineCategory.DAMAGED if outcome == "damaged" else FineCategory.LOST,
                amount=copy.price, note=f"Copy {copy.accession_number} reported {outcome}.",
            )
    return issue


def add_copy(*, book, campus, shelf=None, price=None, acquired_on=None, by=None) -> Copy:
    copy = Copy.objects.create(
        organization_id=book.organization_id, book=book, campus=campus, shelf=shelf, price=price,
        acquired_on=acquired_on, accession_number=_next_number(book.organization_id, "ACC-", Copy),
    )
    log(AuditLog.Action.CREATE, instance=copy, module=MODULE, actor=by)
    return copy


def withdraw_copy(copy: Copy, *, by=None) -> Copy:
    if copy.status == CopyStatus.ISSUED:
        raise ConflictError("This copy is out on loan.", code="issued")
    before = copy.status
    copy.status = CopyStatus.WITHDRAWN
    copy.save(update_fields=["status", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=copy, module=MODULE, actor=by,
        changes={"status": {"before": before, "after": "withdrawn"}})
    return copy


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------
def reserve_book(*, book, member: Member, by=None) -> Reservation:
    if not member.is_active:
        raise ConflictError("This membership isn't active.", code="inactive_member")
    if book.copies.filter(status=CopyStatus.AVAILABLE, campus_id=member.campus_id).exists():
        raise ConflictError("A copy is available right now; no need to reserve it.", code="copy_available")
    if Reservation.objects.filter(book=book, member=member,
                                  status__in=[ReservationStatus.PENDING, ReservationStatus.READY]).exists():
        raise ConflictError("This member already has this book reserved.", code="already_reserved")
    reservation = Reservation.objects.create(organization_id=book.organization_id, book=book, member=member)
    log(AuditLog.Action.CREATE, instance=reservation, module=MODULE, actor=by)
    return reservation


def cancel_reservation(reservation: Reservation, reason: str, *, by=None) -> Reservation:
    if reservation.status not in (ReservationStatus.PENDING, ReservationStatus.READY):
        raise ConflictError("This reservation is no longer active.", code="not_active")
    if not reason.strip():
        raise ServiceError("Say why it's being cancelled.", code="reason_required")
    with transaction.atomic():
        held_copy = reservation.copy if reservation.status == ReservationStatus.READY else None
        reservation.status = ReservationStatus.CANCELLED
        reservation.cancelled_reason = reason.strip()
        reservation.save(update_fields=["status", "cancelled_reason", "updated_at"])
        if held_copy is not None:
            _release_copy(held_copy, by=by)
    return reservation


def fulfil_reservation(reservation: Reservation, *, by=None) -> Issue:
    """The member has come in to collect the copy held for them."""
    if reservation.status != ReservationStatus.READY:
        raise ConflictError("This reservation has no copy being held yet.", code="not_ready")
    with transaction.atomic():
        issue = issue_book(copy=reservation.copy, member=reservation.member, by=by, _expect=CopyStatus.RESERVED)
        reservation.status = ReservationStatus.FULFILLED
        reservation.fulfilled_at = timezone.now()
        reservation.save(update_fields=["status", "fulfilled_at", "updated_at"])
    return issue


def expire_stale_reservations(*, by=None) -> dict:
    """The office's explicit sweep for reservations held past their hold
    window — never a cron, matching finance's ``assess-late-fees``. Safe to
    run repeatedly: only a ``ready`` reservation past ``expires_at`` moves."""
    now = timezone.now()
    expired = list(Reservation.objects.filter(status=ReservationStatus.READY, expires_at__lt=now)
                  .select_related("copy"))
    with transaction.atomic():
        for reservation in expired:
            copy = reservation.copy
            reservation.status = ReservationStatus.EXPIRED
            reservation.save(update_fields=["status", "updated_at"])
            if copy is not None:
                _release_copy(copy, by=by)
    return {"expired": len(expired)}


# ---------------------------------------------------------------------------
# Fines
# ---------------------------------------------------------------------------
def pay_fine(fine: Fine, *, by=None) -> Fine:
    if fine.status != FineStatus.PENDING:
        raise ConflictError("This fine isn't pending.", code="not_pending")
    fine.status = FineStatus.PAID
    fine.paid_at = timezone.now()
    fine.collected_by = by
    fine.save(update_fields=["status", "paid_at", "collected_by", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=fine, module=MODULE, actor=by,
        changes={"status": {"before": "pending", "after": "paid"}})
    return fine


def waive_fine(fine: Fine, reason: str, *, by=None) -> Fine:
    if fine.status != FineStatus.PENDING:
        raise ConflictError("This fine isn't pending.", code="not_pending")
    if not reason.strip():
        raise ServiceError("Say why it's being waived.", code="reason_required")
    fine.status = FineStatus.WAIVED
    fine.waived_at = timezone.now()
    fine.waived_reason = reason.strip()
    fine.waived_by = by
    fine.save(update_fields=["status", "waived_at", "waived_reason", "waived_by", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=fine, module=MODULE, actor=by,
        changes={"status": {"before": "pending", "after": "waived"}})
    return fine


def _notify_reservation_ready(reservation: Reservation) -> None:
    """``ReservationReady`` (claude.md section 26): tell the member a copy
    is being held for them, and until when."""
    from modules.notifications.services import notify

    user = reservation.member.user
    if user is None:
        return
    notify([user], event_type="library.reservation_ready", title=f"Ready to collect: {reservation.book.title}",
          body=f"A copy of {reservation.book.title} is being held for you until "
               f"{reservation.expires_at:%Y-%m-%d}.",
          data={"reservation": reservation.pk}, organization_id=reservation.organization_id)
