"""Marking, submitting and correcting."""
from django.db import IntegrityError, transaction

from tests.factories import create_student

from core.audit.models import AuditLog
from modules.students.services import place_student

from ..models import AttendanceRecord, AttendanceSession
from .base import API, MONDAY, TODAY, AttendanceTestCase


class MarkingTestCase(AttendanceTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.hari)
        self.session = self.open_lesson(self.physics_a).data["id"]
        self.ram_e, self.shyam_e = self.enrollment(self.ram), self.enrollment(self.shyam)

    def statuses(self):
        return dict(AttendanceRecord.objects.filter(session_id=self.session)
                    .values_list("enrollment__student__first_name", "status"))


class MarkTests(MarkingTestCase):
    def test_roster_lists_who_is_expected(self):
        response = self.client.get(f"{API}/sessions/{self.session}/roster/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([s["student_name"] for s in response.data["students"]],
                         ["Ram Student", "Shyam Student"])
        self.assertIsNone(response.data["students"][0]["status"])

    def test_an_elective_expects_only_the_students_who_chose_it(self):
        self.login(self.sita)
        computer = self.open_lesson(self.computer_a).data["id"]

        response = self.client.get(f"{API}/sessions/{computer}/roster/")

        self.assertEqual([s["student_name"] for s in response.data["students"]], ["Ram Student"])
        self.assertError(self.mark(computer, [(self.shyam_e.pk, "present")]), 400, "not_expected")

    def test_mark_some_and_the_rest_present(self):
        response = self.mark(self.session, [(self.shyam_e.pk, "absent")], rest="present")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.statuses(), {"Ram": "present", "Shyam": "absent"})
        record = AttendanceRecord.objects.get(enrollment=self.ram_e)
        self.assertEqual((record.source, record.marked_by), ("teacher", self.hari.user))

    def test_marking_again_before_submitting_just_updates(self):
        self.mark(self.session, [(self.ram_e.pk, "absent")])
        self.mark(self.session, [(self.ram_e.pk, "late")])

        self.assertEqual(self.statuses(), {"Ram": "late"})
        self.assertFalse(AttendanceRecord.objects.get(enrollment=self.ram_e).corrections.exists())

    def test_statuses_beyond_present_and_absent(self):
        self.mark(self.session, [(self.ram_e.pk, "medical_leave"), (self.shyam_e.pk, "on_duty")])

        self.assertEqual(self.statuses(), {"Ram": "medical_leave", "Shyam": "on_duty"})

    def test_a_student_of_another_class_is_refused(self):
        outsider = create_student(self.lalitpur, student_number="S-9", first_name="Out",
                                  admitted_on=self.admitted)
        place_student(student=outsider, section=self.section_b)

        response = self.mark(self.session, [(self.enrollment(outsider).pk, "present")])

        self.assertError(response, 400, "not_expected")
        self.assertEqual(self.statuses(), {})

    def test_a_student_who_joined_after_that_day_is_not_expected(self):
        from datetime import timedelta

        late = create_student(self.lalitpur, student_number="S-8", first_name="Late",
                              admitted_on=MONDAY + timedelta(days=1))
        place_student(student=late, section=self.section_a)

        roster = self.client.get(f"{API}/sessions/{self.session}/roster/").data["students"]

        self.assertNotIn("Late Student", [s["student_name"] for s in roster])

    def test_one_record_per_student_per_session_in_the_database(self):
        self.mark(self.session, [(self.ram_e.pk, "present")])

        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceRecord.objects.create(organization=self.org, session_id=self.session,
                                            enrollment=self.ram_e, status="absent")

    def test_a_retried_offline_sync_is_not_applied_twice(self):
        """The phone sent it, lost the reply, and sends it again later — after
        the teacher had already changed the mark on another device."""
        payload = {"records": [{"enrollment": self.ram_e.pk, "status": "absent",
                                "client_key": "phone-1-rec-1",
                                "recorded_at": f"{MONDAY.isoformat()}T10:05:00Z"}]}
        self.client.post(f"{API}/sessions/{self.session}/mark/", payload)
        self.mark(self.session, [(self.ram_e.pk, "present")])

        retry = self.client.post(f"{API}/sessions/{self.session}/mark/", payload)

        self.assertEqual(retry.status_code, 200)
        self.assertEqual(self.statuses(), {"Ram": "present"})
        record = AttendanceRecord.objects.get(enrollment=self.ram_e)
        self.assertEqual(record.recorded_at.isoformat()[:10], TODAY.isoformat())

    def test_offline_marks_keep_when_they_were_taken(self):
        self.client.post(f"{API}/sessions/{self.session}/mark/", {"records": [
            {"enrollment": self.ram_e.pk, "status": "present", "client_key": "k1",
             "recorded_at": f"{MONDAY.isoformat()}T10:05:00Z"}]})

        record = AttendanceRecord.objects.get(enrollment=self.ram_e)
        self.assertEqual(record.recorded_at.isoformat(), f"{MONDAY.isoformat()}T10:05:00+00:00")
        self.assertEqual(record.client_key, "k1")

    def test_another_teacher_cannot_mark(self):
        self.login(self.sita)

        self.assertError(self.mark(self.session, rest="present"), 403, "not_your_class")

    def test_the_office_marks_as_office(self):
        self.login(self.office)
        self.mark(self.session, rest="present")

        self.assertEqual(AttendanceRecord.objects.get(enrollment=self.ram_e).source, "manual")


class SubmitTests(MarkingTestCase):
    def test_everyone_must_be_marked(self):
        self.mark(self.session, [(self.ram_e.pk, "present")])

        response = self.client.post(f"{API}/sessions/{self.session}/submit/")

        self.assertError(response, 409, "unmarked")
        self.assertEqual([e["name"] for e in response.data["error"]["details"]["enrollments"]],
                         ["Shyam Student"])

    def test_submit_with_the_rest_absent(self):
        self.mark(self.session, [(self.ram_e.pk, "present")])

        response = self.client.post(f"{API}/sessions/{self.session}/submit/", {"rest": "absent"})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "submitted")
        self.assertEqual(self.statuses(), {"Ram": "present", "Shyam": "absent"})
        self.assertTrue(AuditLog.objects.filter(object_type="attendance.AttendanceSession").exists())

    def test_submitting_twice_is_harmless(self):
        self.client.post(f"{API}/sessions/{self.session}/submit/", {"rest": "present"})

        self.assertEqual(self.client.post(f"{API}/sessions/{self.session}/submit/").status_code, 200)

    def test_no_marking_after_submission(self):
        self.client.post(f"{API}/sessions/{self.session}/submit/", {"rest": "present"})

        self.assertError(self.mark(self.session, [(self.ram_e.pk, "absent")]), 409, "session_submitted")

    def test_only_the_office_reopens(self):
        self.client.post(f"{API}/sessions/{self.session}/submit/", {"rest": "present"})

        self.assertEqual(self.client.post(f"{API}/sessions/{self.session}/reopen/").status_code, 403)
        self.login(self.office)
        response = self.client.post(f"{API}/sessions/{self.session}/reopen/")

        self.assertEqual(response.data["status"], "open")


class CorrectionTests(MarkingTestCase):
    def setUp(self):
        super().setUp()
        self.mark(self.session, [(self.shyam_e.pk, "absent")], rest="present")
        self.client.post(f"{API}/sessions/{self.session}/submit/")
        self.record = AttendanceRecord.objects.get(enrollment=self.shyam_e)

    def test_a_correction_needs_a_reason(self):
        response = self.client.patch(f"{API}/records/{self.record.pk}/", {"status": "present"})

        self.assertError(response, 400, "reason_required")

    def test_a_correction_is_kept_as_history(self):
        response = self.client.patch(f"{API}/records/{self.record.pk}/", {
            "status": "present", "reason": "Was in the library, came late",
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "present")
        [correction] = response.data["corrections"]
        self.assertEqual((correction["old_status"], correction["new_status"]), ("absent", "present"))
        self.assertEqual(correction["corrected_by"], self.hari.user.pk)
        audit = AuditLog.objects.get(object_type="attendance.AttendanceRecord")
        self.assertEqual(audit.changes["status"], {"before": "absent", "after": "present"})
        self.assertEqual(audit.metadata["reason"], "Was in the library, came late")

    def test_another_teacher_cannot_correct(self):
        self.login(self.sita)

        response = self.client.patch(f"{API}/records/{self.record.pk}/",
                                     {"status": "present", "reason": "x"})

        self.assertEqual(response.status_code, 403)


class HistoryTests(MarkingTestCase):
    def test_records_stay_with_the_class_the_student_was_in(self):
        """Ram moves to section B later; Monday's record still says section A."""
        self.mark(self.session, rest="present")
        self.client.post(f"{API}/sessions/{self.session}/submit/")
        place_student(student=self.ram, section=self.section_b)

        record = AttendanceRecord.objects.get(enrollment__student=self.ram)

        self.assertEqual(record.enrollment.section, self.section_a)
        self.assertEqual(record.session.section, self.section_a)

    def test_who_marked_it_is_kept_after_a_hand_over(self):
        from modules.timetable.services import hand_over

        self.mark(self.session, rest="present")
        hand_over(assignments=[self.physics_a.teaching_assignment], teacher=self.sita, on=TODAY)

        record = AttendanceRecord.objects.get(enrollment=self.ram_e)
        self.assertEqual(record.marked_by, self.hari.user)
        self.assertEqual(AttendanceSession.objects.get(pk=self.session).teacher, self.hari)
