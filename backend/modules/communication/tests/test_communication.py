from datetime import timedelta

from django.utils import timezone

from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_staff_member,
    create_student,
    create_user,
    user_with_system_role,
)

from ..models import Appointment, AppointmentSlot, MessageThread
from ..services import book_slot, start_thread

API = "/api/v1"


class CommunicationTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.hari = create_staff_member(self.campus, employee_number="E-1", first_name="Hari")
        self.hari_user = user_with_system_role(self.org, "staff", email="hari@kmc.test", campus=self.campus)
        self.hari.user = self.hari_user
        self.hari.save(update_fields=["user"])

        self.ram_user = create_user(self.org, email="ram@kmc.test", user_type="student")
        self.ram = create_student(self.campus, student_number="S-1", first_name="Ram", user=self.ram_user)

        self.rita_user = create_user(self.org, email="rita@kmc.test", user_type="parent")

    def login(self, who):
        self.logout()
        self.authenticate(getattr(who, "user", None) or who)


class MessagingTests(CommunicationTestCase):
    def test_a_staff_member_can_start_a_thread_with_a_student(self):
        self.login(self.hari_user)
        r = self.client.post(f"{API}/communication/threads/", {"other_user": self.ram_user.pk,
                                                                "campus": self.campus.pk, "subject": "Progress"})
        self.assertEqual(r.status_code, 201, r.data)

    def test_a_student_cannot_start_a_thread(self):
        self.login(self.ram_user)
        r = self.client.post(f"{API}/communication/threads/", {"other_user": self.hari_user.pk,
                                                                "campus": self.campus.pk})
        self.assertEqual(r.status_code, 403)

    def test_starting_a_thread_with_another_staff_member_fails(self):
        other_staff = user_with_system_role(self.org, "staff", email="other@kmc.test")
        self.login(self.hari_user)
        r = self.client.post(f"{API}/communication/threads/", {"other_user": other_staff.pk,
                                                                "campus": self.campus.pk})
        self.assertEqual(r.status_code, 400)

    def test_a_student_can_reply_but_not_cold_start(self):
        thread = start_thread(staff_user=self.hari_user, other_user=self.ram_user, campus=self.campus)
        self.login(self.ram_user)
        r = self.client.post(f"{API}/communication/threads/{thread.pk}/messages/", {"body": "Thanks!"})
        self.assertEqual(r.status_code, 201, r.data)

    def test_an_outsider_cannot_read_the_thread(self):
        thread = start_thread(staff_user=self.hari_user, other_user=self.ram_user, campus=self.campus)
        self.login(self.rita_user)
        r = self.client.get(f"{API}/communication/threads/{thread.pk}/")
        self.assertEqual(r.status_code, 404)

    def test_needs_a_login(self):
        self.assertEqual(self.client.get(f"{API}/communication/threads/").status_code, 401)


class AppointmentTests(CommunicationTestCase):
    def make_slot(self, **fields):
        now = timezone.now() + timedelta(days=1)
        return AppointmentSlot.objects.create(
            organization=self.org, campus=self.campus, staff=self.hari, starts_at=now,
            ends_at=now + timedelta(minutes=30), **fields,
        )

    def test_a_student_can_book_an_open_slot(self):
        slot = self.make_slot()
        self.login(self.ram_user)
        r = self.client.post(f"{API}/communication/appointments/", {"slot": slot.pk})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "pending")

    def test_booking_an_already_booked_slot_conflicts(self):
        slot = self.make_slot()
        book_slot(slot, requested_by=self.rita_user)
        self.login(self.ram_user)
        r = self.client.post(f"{API}/communication/appointments/", {"slot": slot.pk})
        self.assertEqual(r.status_code, 409)

    def test_rebooking_after_cancellation_works(self):
        slot = self.make_slot()
        first = book_slot(slot, requested_by=self.rita_user)
        first.status, first.cancelled_reason = "cancelled", "changed mind"
        first.save()
        self.login(self.ram_user)
        r = self.client.post(f"{API}/communication/appointments/", {"slot": slot.pk})
        self.assertEqual(r.status_code, 201, r.data)

    def test_the_staff_member_can_approve(self):
        slot = self.make_slot()
        appointment = book_slot(slot, requested_by=self.ram_user, student=self.ram)
        self.login(self.hari_user)
        r = self.client.post(f"{API}/communication/appointments/{appointment.pk}/approve/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "confirmed")

    def test_another_staff_member_cannot_approve(self):
        other_staff = create_staff_member(self.campus, employee_number="E-2", first_name="Other")
        other_user = user_with_system_role(self.org, "staff", email="other@kmc.test")
        other_staff.user = other_user
        other_staff.save(update_fields=["user"])
        slot = self.make_slot()
        appointment = book_slot(slot, requested_by=self.ram_user, student=self.ram)
        self.login(other_user)
        # Out of their queryset entirely (not their own, not requested by them, and their
        # "staff" role holds only communication.publish_slots, not the office-wide
        # communication.manage_slots override) — a 404, not a 403.
        r = self.client.post(f"{API}/communication/appointments/{appointment.pk}/approve/")
        self.assertEqual(r.status_code, 404)

    def test_the_requester_can_cancel_their_own_booking(self):
        slot = self.make_slot()
        appointment = book_slot(slot, requested_by=self.ram_user, student=self.ram)
        self.login(self.ram_user)
        r = self.client.post(f"{API}/communication/appointments/{appointment.pk}/cancel/", {"reason": "Can't make it"})
        self.assertEqual(r.status_code, 200)

    def test_a_stranger_cannot_cancel(self):
        slot = self.make_slot()
        appointment = book_slot(slot, requested_by=self.ram_user, student=self.ram)
        self.login(self.rita_user)
        r = self.client.post(f"{API}/communication/appointments/{appointment.pk}/cancel/", {"reason": "x"})
        self.assertEqual(r.status_code, 404)  # not in their queryset at all, same reasoning as above

    def test_a_student_cannot_create_a_slot(self):
        self.login(self.ram_user)
        r = self.client.post(f"{API}/communication/appointment-slots/",
                             {"campus": self.campus.pk, "staff": self.hari.pk,
                              "starts_at": "2030-01-01T10:00:00Z", "ends_at": "2030-01-01T10:30:00Z"})
        self.assertEqual(r.status_code, 403)

    def test_a_staff_member_can_publish_a_slot(self):
        self.login(self.hari_user)
        r = self.client.post(f"{API}/communication/appointment-slots/",
                             {"campus": self.campus.pk, "staff": self.hari.pk,
                              "starts_at": "2030-01-01T10:00:00Z", "ends_at": "2030-01-01T10:30:00Z"})
        self.assertEqual(r.status_code, 201, r.data)
