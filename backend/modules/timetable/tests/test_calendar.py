"""Real life: holidays, closures, exam days and make-up days."""
from tests.factories import create_timetable_entry, user_with_system_role

from modules.academics.models import CalendarEvent

from .test_entries import API, MONDAY, TUESDAY, TimetableTestCase
from .test_lesson_changes import next_monday


class CalendarTestCase(TimetableTestCase):
    def setUp(self):
        super().setUp()
        self.monday = next_monday()
        self.a_p1 = create_timetable_entry(self.a_physics, self.p1, MONDAY, room=self.r101)
        self.b_p2 = create_timetable_entry(self.b_physics, self.p2, MONDAY, room=self.r102)
        self.tuesday_lesson = create_timetable_entry(self.a_chemistry, self.p1, TUESDAY, room=self.r101)
        self.org_admin = user_with_system_role(self.org, "org-admin", email="p@kmc.test")

    def event(self, **data):
        data = {"kind": "holiday", "title": "Dashain", "start_date": self.monday.isoformat(),
                "end_date": self.monday.isoformat(), **data}
        return self.client.post(f"{API}/calendar/", data, format="json")

    def day_view(self, **params):
        response = self.client.get(f"{API}/timetable/day/", {"date": self.monday.isoformat(), **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data


class HolidayTests(CalendarTestCase):
    def test_a_holiday_suspends_every_lesson(self):
        self.authenticate(self.org_admin)
        self.assertEqual(self.event().status_code, 201)

        lessons = self.day_view()

        self.assertEqual(len(lessons), 2)
        self.assertTrue(all(x["is_cancelled"] and x["closed_by"] == "Dashain" for x in lessons))

    def test_an_exam_for_one_grade_only(self):
        self.authenticate(self.org_admin)
        other_grade = self.event(kind="exam", title="Grade 12 pre-board", program=self.program.pk, level=12)
        this_grade = self.event(kind="exam", title="Grade 11 mid-term", program=self.program.pk, level=11,
                                campus=self.lalitpur.pk)

        self.assertEqual((other_grade.status_code, this_grade.status_code), (201, 201))
        self.assertTrue(all(x["closed_by"] == "Grade 11 mid-term" for x in self.day_view()))

    def test_an_event_doesnt_stop_classes_unless_told(self):
        self.authenticate(self.org_admin)
        self.event(kind="event", title="Sports day")

        self.assertFalse(any(x["is_cancelled"] for x in self.day_view()))

    def test_no_substitute_on_a_holiday(self):
        self.authenticate(self.org_admin)
        self.event()

        response = self.client.post(f"{API}/lesson-changes/", {
            "entry": self.a_p1.pk, "date": self.monday.isoformat(), "substitute_teacher": self.sita.pk})

        self.assertEqual(response.status_code, 400)
        self.assertIn("Dashain", str(response.data))

    def test_a_campus_closure_cancels_a_teachers_day_there(self):
        self.authenticate(self.org_admin)
        self.event(kind="closure", title="Bandh", campus=self.lalitpur.pk)

        lessons = self.day_view(teacher=self.hari.pk)

        self.assertTrue(lessons and all(x["closed_by"] == "Bandh" for x in lessons))


class MakeupDayTests(CalendarTestCase):
    def test_a_makeup_day_runs_another_weekdays_timetable(self):
        self.authenticate(self.org_admin)
        response = self.event(kind="makeup_day", title="Make-up for Dashain", runs_timetable_of=TUESDAY)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data["suspends_classes"])
        self.assertEqual([x["subject_name"] for x in self.day_view()], ["Chemistry"])

    def test_a_makeup_day_needs_a_weekday(self):
        self.authenticate(self.org_admin)

        self.assertEqual(self.event(kind="makeup_day").status_code, 400)
        self.assertEqual(self.event(kind="holiday", runs_timetable_of=2).status_code, 400)


class CalendarScopeTests(CalendarTestCase):
    def test_campus_admin_manages_own_campus_and_sees_shared_holidays(self):
        self.authenticate(self.org_admin)
        shared = self.event(title="Tihar").data["id"]
        self.authenticate(self.admin)  # campus-admin, Lalitpur

        own = self.event(title="Snow day", kind="closure", campus=self.lalitpur.pk)
        everywhere = self.event(title="Strike", kind="closure")
        elsewhere = self.event(title="Strike", kind="closure", campus=self.bhaktapur.pk)
        listed = {e["title"] for e in self.client.get(f"{API}/calendar/").data["results"]}
        edit_shared = self.client.patch(f"{API}/calendar/{shared}/", {"title": "Tihar!"})

        self.assertEqual(own.status_code, 201, own.data)
        self.assertEqual((everywhere.status_code, elsewhere.status_code), (403, 403))
        self.assertEqual(listed, {"Tihar", "Snow day"})
        self.assertEqual(edit_shared.status_code, 404)

    def test_spans_and_filters(self):
        self.authenticate(self.org_admin)
        self.event(title="Dashain")

        found = self.client.get(f"{API}/calendar/", {"from": self.monday.isoformat(), "to": self.monday.isoformat()})
        none = self.client.get(f"{API}/calendar/", {"to": "2000-01-01"})

        self.assertEqual(found.data["count"], 1)
        self.assertEqual(none.data["count"], 0)

    def test_level_needs_a_program(self):
        self.authenticate(self.org_admin)

        self.assertEqual(self.event(level=11).status_code, 400)
        self.assertEqual(CalendarEvent.objects.count(), 0)
