"""Messaging and Appointments: two lightweight ways staff and
parents/students reach each other directly.

**Messaging** is threaded and 1:1. A thread can only be *started* by staff or
a teacher — a parent or student may only reply inside a thread already opened
with them (``services.start_thread``/``send_message`` enforce this; a plain
create endpoint on ``Message`` would let anyone cold-start a conversation).

**Appointments** are slot-based, the same shape as an exam seat plan: staff
publish an ``AppointmentSlot``, a parent or student books it, and staff can
approve/cancel it. Booking is race-safe (``select_for_update`` on the slot in
``services.book_slot``), the same way ``events.register`` protects capacity.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from core.common.models import OrganizationOwnedModel, TimeStampedModel


class MessageThread(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="message_threads")
    subject = models.CharField(max_length=200, blank=True)
    staff_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    other_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    is_closed = models.BooleanField(default=False)
    last_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "communication_thread"
        ordering = ["-last_message_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["staff_user", "other_user"], name="uniq_thread_pair"),
        ]

    def __str__(self):
        return f"{self.staff_user} <-> {self.other_user}"


class Message(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    thread = models.ForeignKey(MessageThread, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    body = models.TextField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "communication_message"
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.sender}: {self.body[:40]}"


class AppointmentSlot(OrganizationOwnedModel):
    campus = models.ForeignKey("organizations.Campus", on_delete=models.PROTECT, related_name="appointment_slots")
    staff = models.ForeignKey("staff.StaffMember", on_delete=models.CASCADE, related_name="appointment_slots")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    location = models.CharField(max_length=200, blank=True)
    is_cancelled = models.BooleanField(default=False)

    class Meta:
        db_table = "communication_appointment_slot"
        ordering = ["starts_at", "pk"]
        indexes = [models.Index(fields=["organization", "campus", "starts_at"])]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="slot_dates_ordered"),
        ]

    def __str__(self):
        return f"{self.staff} @ {self.starts_at:%Y-%m-%d %H:%M}"


class AppointmentStatus(models.TextChoices):
    PENDING = "pending", "Waiting for staff"
    CONFIRMED = "confirmed", "Confirmed"
    CANCELLED = "cancelled", "Cancelled"
    COMPLETED = "completed", "Completed"


class Appointment(TimeStampedModel):
    """One booking of a slot. ``student`` is who the appointment is about —
    set when a parent books on a child's behalf, empty when a student books
    for themselves."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="+")
    # Not OneToOne: a cancelled booking must free the slot for a new one, the same way
    # EventRegistration's uniq_live_event_registration excludes "withdrawn" rather than
    # forbidding a second row outright.
    slot = models.ForeignKey(AppointmentSlot, on_delete=models.CASCADE, related_name="appointments")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    student = models.ForeignKey("students.Student", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="appointments")
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=AppointmentStatus.choices, default=AppointmentStatus.PENDING,
                              db_index=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "communication_appointment"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["slot"], condition=~Q(status="cancelled"),
                                    name="uniq_active_appointment_per_slot"),
        ]

    def __str__(self):
        return f"{self.requested_by} — {self.slot}"
