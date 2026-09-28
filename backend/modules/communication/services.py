"""Messaging and appointment rules. Views and serializers call these so a
rule lives in one place.
"""
from django.db import transaction
from django.utils import timezone

from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.permissions.selectors import campus_ids_with_permission
from modules.staff.selectors import staff_member_for_user

from .models import Appointment, AppointmentSlot, AppointmentStatus, Message, MessageThread

MODULE = "communication"
PUBLISH = "communication.publish_slots"
MANAGE = "communication.manage_slots"


def is_staff_side(user) -> bool:
    return staff_member_for_user(user) is not None


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------
def start_thread(*, staff_user, other_user, campus, subject: str = "") -> MessageThread:
    if not is_staff_side(staff_user):
        raise PermissionDeniedError("Only a staff member can start a conversation.", code="not_staff")
    if other_user.user_type not in ("student", "parent"):
        raise ServiceError("A conversation can only be started with a student or a parent.", code="bad_recipient")
    thread, created = MessageThread.objects.get_or_create(
        staff_user=staff_user, other_user=other_user,
        defaults={"organization_id": staff_user.organization_id, "campus": campus, "subject": subject},
    )
    if not created and thread.is_closed:
        thread.is_closed = False
        thread.save(update_fields=["is_closed", "updated_at"])
    return thread


def ensure_participant(user, thread: MessageThread) -> None:
    if user.pk not in (thread.staff_user_id, thread.other_user_id):
        raise PermissionDeniedError("You're not part of this conversation.", code="not_yours")


def send_message(thread: MessageThread, sender, body: str) -> Message:
    ensure_participant(sender, thread)
    if not body.strip():
        raise ServiceError("Say something.", code="empty_message")
    with transaction.atomic():
        message = Message.objects.create(
            organization_id=thread.organization_id, thread=thread, sender=sender, body=body.strip())
        thread.last_message_at, thread.is_closed = message.created_at, False
        thread.save(update_fields=["last_message_at", "is_closed", "updated_at"])
    return message


def close_thread(thread: MessageThread) -> MessageThread:
    if thread.is_closed:
        raise ConflictError("Already closed.", code="already_closed")
    thread.is_closed = True
    thread.save(update_fields=["is_closed", "updated_at"])
    return thread


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------
def book_slot(slot: AppointmentSlot, *, requested_by, student=None, reason: str = "") -> Appointment:
    with transaction.atomic():
        slot = AppointmentSlot.objects.select_for_update().get(pk=slot.pk)
        if slot.is_cancelled:
            raise ConflictError("This slot has been cancelled.", code="slot_cancelled")
        if Appointment.objects.filter(slot=slot).exclude(status=AppointmentStatus.CANCELLED).exists():
            raise ConflictError("This slot is already booked.", code="slot_taken")
        appointment = Appointment.objects.create(
            organization_id=slot.organization_id, slot=slot, requested_by=requested_by, student=student,
            reason=reason,
        )
    return appointment


def ensure_can_manage_appointment(user, appointment: Appointment) -> None:
    """This slot's own staff member, or anyone holding ``communication.manage_slots``
    at that campus — the same "own record, or the office" shape as ``events.can_run``."""
    staff = staff_member_for_user(user)
    if staff is not None and appointment.slot.staff_id == staff.pk:
        return
    campus_ids = campus_ids_with_permission(user, MANAGE)
    if campus_ids is None or appointment.slot.campus_id in campus_ids:
        return
    raise PermissionDeniedError("Only this slot's staff member, or the office, can do this.", code="not_yours")


def ensure_can_cancel(user, appointment: Appointment) -> None:
    if appointment.requested_by_id == user.pk:
        return
    ensure_can_manage_appointment(user, appointment)


def approve_appointment(appointment: Appointment, *, by) -> Appointment:
    ensure_can_manage_appointment(by, appointment)
    if appointment.status != AppointmentStatus.PENDING:
        raise ConflictError("Only a pending appointment can be approved.", code="not_pending")
    appointment.status, appointment.decided_at = AppointmentStatus.CONFIRMED, timezone.now()
    appointment.save(update_fields=["status", "decided_at", "updated_at"])
    return appointment


def cancel_appointment(appointment: Appointment, reason: str) -> Appointment:
    if appointment.status == AppointmentStatus.CANCELLED:
        raise ConflictError("Already cancelled.", code="already_cancelled")
    if not reason.strip():
        raise ServiceError("Say why it's being cancelled.", code="reason_required")
    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancelled_reason, appointment.decided_at = reason.strip(), timezone.now()
    appointment.save(update_fields=["status", "cancelled_reason", "decided_at", "updated_at"])
    return appointment


def complete_appointment(appointment: Appointment, *, by) -> Appointment:
    ensure_can_manage_appointment(by, appointment)
    if appointment.status != AppointmentStatus.CONFIRMED:
        raise ConflictError("Only a confirmed appointment can be completed.", code="not_confirmed")
    appointment.status = AppointmentStatus.COMPLETED
    appointment.save(update_fields=["status", "updated_at"])
    return appointment
