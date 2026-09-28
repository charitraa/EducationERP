"""Ticket lifecycle: ``open -> in_progress -> resolved -> closed``. Views and
serializers call these so a rule lives in one place.
"""
from django.utils import timezone

from core.common.exceptions import ConflictError, PermissionDeniedError
from core.permissions.selectors import campus_ids_with_permission

from .models import SupportTicket, TicketStatus

MODULE = "support"
MANAGE = "support.manage"


def resolve_campus_for(user):
    """The raiser's own campus, when one can be told: a student's or staff
    member's own, or the first of a parent's linked children's. ``None``
    (an org-wide ticket) otherwise — picking one campus for a parent with
    several children isn't important, this is only ever a filter, not an
    access boundary."""
    from modules.parents.selectors import links_for_parent, parent_for_user
    from modules.staff.selectors import staff_member_for_user
    from modules.students.selectors import student_for_user

    student = student_for_user(user)
    if student is not None:
        return student.campus
    staff = staff_member_for_user(user)
    if staff is not None:
        return staff.campus
    parent = parent_for_user(user)
    if parent is not None:
        link = links_for_parent(parent).first()
        if link is not None:
            return link.student.campus
    return None


def holds_manage(user, campus_id) -> bool:
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, MANAGE)
    return campus_ids is None or campus_id in campus_ids


def can_see(user, ticket: SupportTicket) -> bool:
    return (ticket.raised_by_id == user.pk or ticket.assigned_to_id == user.pk
            or holds_manage(user, ticket.campus_id))


def ensure_can_manage(user, ticket: SupportTicket) -> None:
    if not holds_manage(user, ticket.campus_id):
        raise PermissionDeniedError("Only the office can do this.", code="not_office")


def ensure_can_close(user, ticket: SupportTicket) -> None:
    if ticket.raised_by_id != user.pk and not holds_manage(user, ticket.campus_id):
        raise PermissionDeniedError("Only the person who raised this, or the office, can close it.",
                                    code="not_yours")


def assign_ticket(ticket: SupportTicket, assignee, *, by=None) -> SupportTicket:
    if ticket.status == TicketStatus.CLOSED:
        raise ConflictError("This ticket is closed.", code="closed")
    ticket.assigned_to = assignee
    if ticket.status == TicketStatus.OPEN:
        ticket.status = TicketStatus.IN_PROGRESS
    ticket.save(update_fields=["assigned_to", "status", "updated_at"])
    return ticket


def resolve_ticket(ticket: SupportTicket, *, by=None) -> SupportTicket:
    if ticket.status not in (TicketStatus.OPEN, TicketStatus.IN_PROGRESS):
        raise ConflictError("Only an open ticket can be resolved.", code="not_open")
    ticket.status, ticket.resolved_at = TicketStatus.RESOLVED, timezone.now()
    ticket.save(update_fields=["status", "resolved_at", "updated_at"])
    return ticket


def close_ticket(ticket: SupportTicket, *, by=None) -> SupportTicket:
    if ticket.status == TicketStatus.CLOSED:
        raise ConflictError("Already closed.", code="already_closed")
    ticket.status, ticket.closed_at = TicketStatus.CLOSED, timezone.now()
    ticket.save(update_fields=["status", "closed_at", "updated_at"])
    return ticket
