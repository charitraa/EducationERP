"""Checking students in, and recording their role in an event."""
from ..models import EventAttendance, EventParticipation
from .base import API, EventTestCase

EVENTS = f"{API}/events/"


class AttendanceTestCase(EventTestCase):
    def setUp(self):
        super().setUp()
        self.event = self.make_event()

    def mark(self, entries, who=None):
        self.login(who or self.hari_user)
        return self.client.post(f"{EVENTS}{self.event.pk}/mark-attendance/", {"entries": entries}, format="json")


class MarkAttendanceTests(AttendanceTestCase):
    def test_the_organizer_marks_attendance(self):
        r = self.mark([{"student": self.ram.pk, "status": "present"}, {"student": self.shyam.pk, "status": "absent"}])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual({row["student"]: row["status"] for row in r.data},
                         {self.ram.pk: "present", self.shyam.pk: "absent"})

    def test_the_office_can_also_mark(self):
        r = self.mark([{"student": self.ram.pk, "status": "present"}], who=self.office)
        self.assertEqual(r.status_code, 200, r.data)

    def test_someone_else_cannot_mark(self):
        from tests.factories import create_staff_member, user_with_system_role

        staff = create_staff_member(self.campus, employee_number="E-9", first_name="Other")
        user = user_with_system_role(self.org, "staff", email="other@kmc.test")
        staff.user = user
        staff.save(update_fields=["user"])
        r = self.mark([{"student": self.ram.pk, "status": "present"}], who=user)
        self.assertError(r, 403, "not_your_event")

    def test_a_student_is_not_listed_twice(self):
        r = self.mark([{"student": self.ram.pk, "status": "present"}, {"student": self.ram.pk, "status": "absent"}])
        self.assertEqual(r.status_code, 400)

    def test_needs_at_least_one_student(self):
        r = self.mark([])
        self.assertEqual(r.status_code, 400)

    def test_remarking_updates_the_record_without_duplicating_it(self):
        self.mark([{"student": self.ram.pk, "status": "present"}])
        self.mark([{"student": self.ram.pk, "status": "absent"}])
        self.assertEqual(EventAttendance.objects.filter(event=self.event, student=self.ram).count(), 1)
        self.assertEqual(EventAttendance.objects.get(event=self.event, student=self.ram).status, "absent")

    def test_attendance_and_roster_show_what_was_marked(self):
        self.mark([{"student": self.ram.pk, "status": "present"}])
        self.login(self.office)
        r = self.client.get(f"{EVENTS}{self.event.pk}/attendance/")
        self.assertEqual([row["student"] for row in r.data], [self.ram.pk])
        roster = self.client.get(f"{EVENTS}{self.event.pk}/roster/")
        row = next(x for x in roster.data if x["student"] == self.ram.pk)
        self.assertEqual(row["attendance_status"], "present")


class ParticipationTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.event = self.make_event()

    def record(self, student, role, who=None, **extra):
        self.login(who or self.hari_user)
        return self.client.post(f"{EVENTS}{self.event.pk}/record-participation/",
                                {"student": student.pk, "role": role, **extra})

    def test_record_a_role(self):
        r = self.record(self.ram, "winner", position=1, remark="Best speech")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["role"], r.data["position"]), ("winner", 1))

    def test_a_student_can_hold_two_different_roles(self):
        self.record(self.ram, "participant")
        r = self.record(self.ram, "volunteer")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(EventParticipation.objects.filter(event=self.event, student=self.ram).count(), 2)

    def test_recording_the_same_role_again_updates_it(self):
        self.record(self.ram, "winner", position=1)
        r = self.record(self.ram, "winner", position=2, remark="Corrected")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(EventParticipation.objects.filter(event=self.event, student=self.ram).count(), 1)
        self.assertEqual(r.data["position"], 2)

    def test_someone_else_cannot_record(self):
        from tests.factories import create_staff_member, user_with_system_role

        staff = create_staff_member(self.campus, employee_number="E-9", first_name="Other")
        user = user_with_system_role(self.org, "staff", email="other@kmc.test")
        staff.user = user
        staff.save(update_fields=["user"])
        r = self.record(self.ram, "winner", who=user)
        self.assertError(r, 403, "not_your_event")

    def test_participation_list_and_roster(self):
        self.record(self.ram, "winner")
        self.login(self.office)
        r = self.client.get(f"{EVENTS}{self.event.pk}/participation/")
        self.assertEqual([row["role"] for row in r.data], ["winner"])
        roster = self.client.get(f"{EVENTS}{self.event.pk}/roster/")
        row = next(x for x in roster.data if x["student"] == self.ram.pk)
        self.assertEqual(row["roles"], ["winner"])
