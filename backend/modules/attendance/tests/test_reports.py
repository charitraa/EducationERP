"""Reports: percentages, the register, defaulters, what's missing, and the
student's and parent's own view."""
from datetime import timedelta

from tests.factories import (
    create_attendance_record,
    create_attendance_session,
    create_calendar_event,
    create_lesson_change,
    create_parent,
    create_user,
    user_with_system_role,
)

from modules.parents.services import link_student

from ..selectors import summarize
from .base import API, MONDAY, AttendanceTestCase


class SummaryTests(AttendanceTestCase):
    def test_excused_absences_are_left_out_of_the_percentage(self):
        summary = summarize({"present": 6, "late": 1, "absent": 2, "medical_leave": 1})

        self.assertEqual((summary["total"], summary["attended"]), (10, 7))
        self.assertEqual(summary["percentage"], 77.8)  # 7 of 9

    def test_no_countable_sessions_has_no_percentage(self):
        self.assertIsNone(summarize({"leave": 3})["percentage"])


class ReportTestCase(AttendanceTestCase):
    """Four Mondays of Physics for section A: Ram misses one, Shyam three."""

    def setUp(self):
        super().setUp()
        ram, shyam = self.enrollment(self.ram), self.enrollment(self.shyam)
        self.days = [MONDAY - timedelta(weeks=w) for w in range(4)]
        for number, day in enumerate(self.days):
            session = create_attendance_session(self.section_a, day, entry=self.physics_a)
            create_attendance_record(session, ram, "absent" if number == 0 else "present")
            create_attendance_record(session, shyam, "present" if number == 0 else "absent")
        computer = create_attendance_session(self.section_a, MONDAY, entry=self.computer_a)
        create_attendance_record(computer, ram, "present")
        self.login(self.office)


def _submitted(session):
    from django.utils import timezone

    session.status, session.submitted_at = "submitted", timezone.now()
    session.save()
    return session


class StudentReportTests(ReportTestCase):
    def test_overall_and_per_subject(self):
        response = self.client.get(f"{API}/reports/student/", {
            "student": self.ram.pk, "from": self.days[-1].isoformat(), "to": MONDAY.isoformat()})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["overall"]["percentage"], 80.0)
        subjects = {s["subject_name"]: s["percentage"] for s in response.data["subjects"]}
        self.assertEqual(subjects, {"Computer Science": 100.0, "Physics": 75.0})

    def test_a_student_at_another_campus_is_unknown_to_a_campus_admin(self):
        from tests.factories import create_student

        elsewhere = create_student(self.bhaktapur, student_number="B-1")

        response = self.client.get(f"{API}/reports/student/", {"student": elsewhere.pk})

        self.assertEqual(response.status_code, 404)


class RegisterTests(ReportTestCase):
    def test_the_class_register(self):
        response = self.client.get(f"{API}/reports/register/", {
            "section": self.section_a.pk, "from": self.days[-1].isoformat(), "to": MONDAY.isoformat()})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["sessions"]), 5)
        rows = {r["student_name"]: r for r in response.data["students"]}
        self.assertEqual(rows["Ram Student"]["marks"].count("present"), 4)
        self.assertEqual(rows["Shyam Student"]["percentage"], 25.0)

    def test_defaulters_below_75_percent(self):
        response = self.client.get(f"{API}/reports/defaulters/", {
            "section": self.section_a.pk, "from": self.days[-1].isoformat(), "to": MONDAY.isoformat(),
            "below": 75})

        self.assertEqual([s["student_name"] for s in response.data["students"]], ["Shyam Student"])

    def test_teachers_without_view_permission_cannot_read_reports(self):
        self.login(self.hari)

        response = self.client.get(f"{API}/reports/register/", {"section": self.section_a.pk})

        self.assertEqual(response.status_code, 403)

    def test_a_long_span_is_refused(self):
        response = self.client.get(f"{API}/reports/register/", {
            "section": self.section_a.pk, "from": "2020-01-01", "to": "2026-01-01"})

        self.assertEqual(response.status_code, 400)


class MissingTests(AttendanceTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def missing(self):
        response = self.client.get(f"{API}/reports/missing/", {"date": MONDAY.isoformat()})
        self.assertEqual(response.status_code, 200, response.data)
        return {(m["kind"], m["section_name"], m["subject_name"]) for m in response.data["missing"]}

    def test_untaken_lessons_and_roll_calls(self):
        self.assertEqual(self.missing(), {
            ("lesson", "Grade 11 A", "Physics"), ("lesson", "Grade 11 A", "Computer Science"),
            ("daily", "Grade 5 A", None),
        })

    def test_submitted_ones_are_done_and_open_ones_still_missing(self):
        _submitted(create_attendance_session(self.section_a, MONDAY, entry=self.physics_a))
        create_attendance_session(self.grade5, MONDAY)

        missing = self.missing()

        self.assertNotIn(("lesson", "Grade 11 A", "Physics"), missing)
        self.assertIn(("daily", "Grade 5 A", None), missing)

    def test_cancelled_lessons_and_holidays_are_not_missing(self):
        create_lesson_change(self.computer_a, MONDAY, is_cancelled=True)
        create_calendar_event(self.org, title="Sports day", day=MONDAY, kind="event",
                              suspends_classes=True, program=self.school)

        self.assertEqual(self.missing(), {("lesson", "Grade 11 A", "Physics")})


class MyAttendanceTests(ReportTestCase):
    def test_a_student_sees_their_own(self):
        user = user_with_system_role(self.org, "student", email="ram.s@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)

        response = self.client.get(f"{API}/records/me/", {"from": self.days[-1].isoformat()})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["records"]), 5)
        self.assertEqual({r["student_name"] for r in response.data["records"]}, {"Ram Student"})

    def test_a_parent_sees_their_childs(self):
        parent = create_parent(self.org)
        link_student(parent=parent, student=self.shyam, relationship="mother")
        user = create_user(self.org, email="mum@kmc.test")
        parent.user = user
        parent.save(update_fields=["user"])
        self.login(user)

        response = self.client.get(f"{API}/records/me/", {"from": self.days[-1].isoformat()})

        self.assertEqual(response.data["summary"]["student"], self.shyam.pk)
        self.assertEqual(response.data["summary"]["overall"]["percentage"], 25.0)

    def test_a_parent_cannot_pick_someone_elses_child(self):
        parent = create_parent(self.org)
        link_student(parent=parent, student=self.shyam, relationship="mother")
        user = create_user(self.org, email="mum@kmc.test")
        parent.user = user
        parent.save(update_fields=["user"])
        self.login(user)

        response = self.client.get(f"{API}/records/me/", {"student": self.ram.pk})

        self.assertEqual(response.status_code, 404)
