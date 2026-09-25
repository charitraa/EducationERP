"""Opening attendance: the right teacher, the right day, the right mode."""
from datetime import timedelta

from tests.factories import create_calendar_event, create_lesson_change, create_section

from ..models import AttendanceSession
from .base import API, MONDAY, SUNDAY, TODAY, AttendanceTestCase


class LessonSessionTests(AttendanceTestCase):
    def test_the_teacher_opens_their_lesson(self):
        self.login(self.hari)

        response = self.open_lesson(self.physics_a)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["kind"], "lesson")
        self.assertEqual(response.data["subject_name"], "Physics")
        self.assertEqual(response.data["teacher"], self.hari.pk)
        self.assertEqual(response.data["status"], "open")

    def test_opening_again_returns_the_same_session(self):
        """A double tap, a retry or a colleague: one session, never two."""
        self.login(self.hari)
        first = self.open_lesson(self.physics_a)

        second = self.open_lesson(self.physics_a)

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["id"], first.data["id"])
        self.assertEqual(AttendanceSession.objects.count(), 1)

    def test_another_teacher_cannot_open_it(self):
        self.login(self.sita)

        self.assertError(self.open_lesson(self.physics_a), 403, "not_your_class")
        self.assertFalse(AttendanceSession.objects.exists())

    def test_a_substitute_takes_the_lesson_they_cover(self):
        create_lesson_change(self.physics_a, MONDAY, substitute_teacher=self.sita)

        self.login(self.hari)
        self.assertError(self.open_lesson(self.physics_a), 403, "not_your_class")
        self.login(self.sita)
        response = self.open_lesson(self.physics_a)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["teacher"], self.sita.pk)

    def test_the_office_can_open_any_class(self):
        self.login(self.office)

        self.assertEqual(self.open_lesson(self.physics_a).status_code, 201)

    def test_a_campus_admin_elsewhere_cannot(self):
        from tests.factories import user_with_system_role

        other = user_with_system_role(self.org, "campus-admin", email="bkt@kmc.test",
                                      campus=self.bhaktapur)
        self.login(other)

        self.assertEqual(self.open_lesson(self.physics_a).status_code, 403)

    def test_not_for_a_future_date(self):
        self.login(self.hari)

        self.assertError(self.open_lesson(self.physics_a, TODAY + timedelta(days=7)), 400, "future_date")

    def test_not_on_a_day_the_lesson_does_not_run(self):
        self.login(self.hari)

        self.assertError(self.open_lesson(self.physics_a, SUNDAY), 400, "no_lesson")

    def test_not_for_a_cancelled_lesson(self):
        create_lesson_change(self.physics_a, MONDAY, is_cancelled=True)
        self.login(self.hari)

        self.assertError(self.open_lesson(self.physics_a), 409, "lesson_cancelled")

    def test_not_on_a_holiday(self):
        create_calendar_event(self.org, title="Dashain", day=MONDAY, kind="holiday",
                              suspends_classes=True)
        self.login(self.hari)

        response = self.open_lesson(self.physics_a)

        self.assertError(response, 409, "lesson_cancelled")
        self.assertIn("Dashain", response.data["error"]["message"])

    def test_a_school_program_takes_no_lesson_attendance(self):
        from tests.factories import create_teaching_assignment, create_timetable_entry

        maths = create_timetable_entry(create_teaching_assignment(self.grade5, self.physics, self.gita),
                                       self.p1, 1)
        self.login(self.office)

        self.assertError(self.open_lesson(maths), 400, "wrong_mode")

    def test_another_organizations_lesson_is_unknown(self):
        from tests.factories import create_organization

        from modules.timetable.models import TimetableEntry

        other = create_organization(code="other")
        TimetableEntry.objects.filter(pk=self.physics_a.pk).update(organization=other)
        self.login(self.office)

        response = self.open_lesson(self.physics_a)

        self.assertEqual(response.status_code, 400)
        self.assertIn("timetable_entry", response.data["error"]["details"])


class DailySessionTests(AttendanceTestCase):
    def test_the_class_teacher_opens_the_roll_call(self):
        self.login(self.gita)

        response = self.open_daily(self.grade5)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["kind"], response.data["teacher"]), ("daily", self.gita.pk))

    def test_another_teacher_cannot(self):
        self.login(self.hari)

        self.assertError(self.open_daily(self.grade5), 403, "not_your_class")

    def test_a_college_program_takes_no_roll_call(self):
        self.login(self.office)

        self.assertError(self.open_daily(self.section_a), 400, "wrong_mode")

    def test_a_school_without_a_timetable_takes_it_any_open_day(self):
        self.login(self.gita)

        self.assertEqual(self.open_daily(self.grade5, SUNDAY).status_code, 201)

    def test_with_a_timetable_only_on_days_with_lessons(self):
        from tests.factories import create_teaching_assignment, create_timetable_entry

        create_timetable_entry(create_teaching_assignment(self.grade5, self.physics, self.gita),
                               self.p1, 1)
        self.login(self.gita)

        self.assertEqual(self.open_daily(self.grade5, MONDAY).status_code, 201)
        self.assertError(self.open_daily(self.grade5, SUNDAY), 409, "no_classes")

    def test_not_when_the_campus_is_closed(self):
        create_calendar_event(self.org, title="Strike", day=MONDAY, kind="closure",
                              suspends_classes=True, campus=self.lalitpur)
        self.login(self.gita)

        response = self.open_daily(self.grade5)

        self.assertError(response, 409, "no_classes")
        self.assertIn("Strike", response.data["error"]["message"])

    def test_an_exam_day_for_another_grade_does_not_stop_it(self):
        create_calendar_event(self.org, title="Grade 10 exams", day=MONDAY, kind="exam",
                              suspends_classes=True, program=self.school, level=10)
        self.login(self.gita)

        self.assertEqual(self.open_daily(self.grade5).status_code, 201)

    def test_not_outside_the_academic_year(self):
        from tests.factories import create_academic_year

        old = create_academic_year(self.org, name="Old", start=TODAY - timedelta(days=800),
                                   end=TODAY - timedelta(days=436))
        old_section = create_section(self.lalitpur, self.school, old, level=5, name="A",
                                     class_teacher=self.gita)
        self.login(self.gita)

        self.assertError(self.open_daily(old_section), 400, "outside_year")


class MyClassesTests(AttendanceTestCase):
    def test_a_teacher_sees_their_lessons_and_roll_calls(self):
        self.grade5.class_teacher = self.hari
        self.grade5.save()
        self.login(self.hari)
        opened = self.open_lesson(self.physics_a).data["id"]

        response = self.client.get(f"{API}/sessions/mine/", {"date": MONDAY.isoformat()})

        self.assertEqual(response.status_code, 200, response.data)
        classes = {(c["kind"], c["section_name"]): c for c in response.data["classes"]}
        self.assertEqual(classes[("lesson", "Grade 11 A")]["session"], opened)
        self.assertIsNone(classes[("daily", "Grade 5 A")]["session"])

    def test_a_covered_lesson_moves_to_the_substitute(self):
        create_lesson_change(self.physics_a, MONDAY, substitute_teacher=self.sita)

        self.login(self.hari)
        hari = self.client.get(f"{API}/sessions/mine/", {"date": MONDAY.isoformat()}).data["classes"]
        self.login(self.sita)
        sita = self.client.get(f"{API}/sessions/mine/", {"date": MONDAY.isoformat()}).data["classes"]

        self.assertEqual(hari, [])
        self.assertEqual(sorted(c["subject_name"] for c in sita), ["Computer Science", "Physics"])


class TimeZoneTests(AttendanceTestCase):
    def test_today_is_the_organizations_today(self):
        """20:00 UTC is already 01:45 the next day in Kathmandu: a school
        there can open that day's roll call, though the server's date lags."""
        from datetime import datetime, timezone as dt_timezone
        from unittest import mock

        self.org.timezone = "Asia/Kathmandu"
        self.org.save(update_fields=["timezone"])
        utc_day = TODAY - timedelta(days=3)
        now = datetime(utc_day.year, utc_day.month, utc_day.day, 20, 0, tzinfo=dt_timezone.utc)
        self.login(self.gita)

        with mock.patch("django.utils.timezone.now", return_value=now):
            response = self.client.post(f"{API}/sessions/", {"section": self.grade5.pk})

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["date"], (utc_day + timedelta(days=1)).isoformat())
