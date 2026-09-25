"""Students scanning the code on the teacher's screen."""
import time
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from tests.factories import create_parent, create_user, user_with_system_role

from modules.parents.services import link_student

from .. import qr
from ..models import AttendanceRecord
from .base import API, AttendanceTestCase

# Two points about 1.1 km apart in Lalitpur.
CLASSROOM = (27.6710, 85.3240)
NEARBY = (27.6711, 85.3241)
FAR_AWAY = (27.6810, 85.3240)


class QRTestCase(AttendanceTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.hari)
        self.session = self.open_lesson(self.physics_a).data["id"]
        self.ram_user = self.account(self.ram, "ram")

    def account(self, student, name):
        user = user_with_system_role(self.org, "student", email=f"{name}.student@kmc.test")
        student.user = user
        student.save(update_fields=["user"])
        return user

    def code(self, **options):
        self.login(self.hari)
        response = self.client.post(f"{API}/sessions/{self.session}/qr/", options)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["token"]

    def scan(self, user, token, **extra):
        self.login(user)
        return self.client.post(f"{API}/sessions/scan/", {"token": token, **extra})


class ScanTests(QRTestCase):
    def test_scanning_marks_the_student_present(self):
        response = self.scan(self.ram_user, self.code())

        self.assertEqual(response.status_code, 201, response.data)
        record = AttendanceRecord.objects.get(enrollment__student=self.ram)
        self.assertEqual((record.status, record.source, record.marked_by),
                         ("present", "qr", self.ram_user))

    def test_the_student_comes_from_the_login_not_the_request(self):
        """Ram can't mark Shyam by sending Shyam's id: it's ignored."""
        self.scan(self.ram_user, self.code(), student=self.shyam.pk,
                  enrollment=self.enrollment(self.shyam).pk)

        self.assertEqual(list(AttendanceRecord.objects.values_list("enrollment__student", flat=True)),
                         [self.ram.pk])

    def test_scanning_twice_says_already_marked(self):
        token = self.code()
        self.scan(self.ram_user, token)

        response = self.scan(self.ram_user, token)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["already_marked"])
        self.assertEqual(AttendanceRecord.objects.count(), 1)

    def test_a_teachers_mark_is_not_overwritten_by_a_scan(self):
        token = self.code()
        self.mark(self.session, [(self.enrollment(self.ram).pk, "absent")])

        self.scan(self.ram_user, token)

        self.assertEqual(AttendanceRecord.objects.get().status, "absent")

    def test_an_expired_code_is_refused(self):
        token = self.code(ttl=15)

        with mock.patch.object(qr.time, "time", return_value=time.time() + 16):
            response = self.scan(self.ram_user, token)

        self.assertError(response, 403, "qr_expired")

    def test_an_edited_code_is_refused(self):
        token = self.code()

        self.assertError(self.scan(self.ram_user, token[:-2] + "xx"), 400, "invalid_qr")

    def test_a_student_of_another_class_is_refused(self):
        from modules.students.services import place_student
        from tests.factories import create_student

        other = create_student(self.lalitpur, student_number="S-7", first_name="Other",
                               admitted_on=self.admitted)
        place_student(student=other, section=self.section_b)

        self.assertError(self.scan(self.account(other, "other"), self.code()), 403, "not_in_class")

    def test_an_elective_code_refuses_students_who_do_not_take_it(self):
        self.login(self.sita)
        self.session = self.open_lesson(self.computer_a).data["id"]
        token = self.client.post(f"{API}/sessions/{self.session}/qr/").data["token"]

        self.assertError(self.scan(self.account(self.shyam, "shyam"), token), 403, "not_in_class")

    def test_only_student_accounts_scan(self):
        parent = create_parent(self.org)
        link_student(parent=parent, student=self.ram, relationship="father")
        dad = create_user(self.org, email="dad@kmc.test")
        parent.user = dad
        parent.save(update_fields=["user"])

        self.assertError(self.scan(dad, self.code()), 403, "not_a_student")
        self.assertError(self.scan(self.hari.user, self.code()), 403, "not_a_student")

    def test_after_submission_scans_are_refused(self):
        token = self.code()
        self.client.post(f"{API}/sessions/{self.session}/submit/", {"rest": "absent"})

        self.assertError(self.scan(self.ram_user, token), 409, "session_submitted")

    def test_one_phone_cannot_mark_two_students(self):
        """A friend signs in on Ram's phone to mark themselves too."""
        token = self.code()
        self.scan(self.ram_user, token, device_id="phone-ram")

        response = self.scan(self.account(self.shyam, "shyam"), token, device_id="phone-ram")

        self.assertError(response, 403, "device_used")

    def test_scans_after_the_cutoff_are_late(self):
        token = self.code(late_after=(timezone.now() - timedelta(minutes=1)).isoformat())

        self.scan(self.ram_user, token)

        self.assertEqual(AttendanceRecord.objects.get().status, "late")

    def test_only_the_days_teacher_shows_a_code(self):
        self.login(self.sita)

        self.assertEqual(self.client.post(f"{API}/sessions/{self.session}/qr/").status_code, 403)


class LocationTests(QRTestCase):
    def located_code(self):
        return self.code(latitude=CLASSROOM[0], longitude=CLASSROOM[1], radius=100)

    def test_a_scan_in_the_classroom_counts(self):
        response = self.scan(self.ram_user, self.located_code(),
                             latitude=NEARBY[0], longitude=NEARBY[1])

        self.assertEqual(response.status_code, 201, response.data)

    def test_a_scan_from_home_is_refused(self):
        """The code was shared on a group chat."""
        response = self.scan(self.ram_user, self.located_code(),
                             latitude=FAR_AWAY[0], longitude=FAR_AWAY[1])

        self.assertError(response, 403, "too_far")

    def test_location_is_required_when_the_code_has_one(self):
        self.assertError(self.scan(self.ram_user, self.located_code()), 403, "location_required")

    def test_distance(self):
        self.assertAlmostEqual(qr.distance_m(*CLASSROOM, *FAR_AWAY), 1112, delta=5)
