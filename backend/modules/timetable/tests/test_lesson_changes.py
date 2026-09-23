"""Substitutions, room changes and cancellations on one date, and the day view."""
from datetime import date, timedelta

from tests.factories import (
    create_staff_member,
    create_teaching_assignment,
    create_timetable_entry,
    user_with_system_role,
)

from ..models import LessonChange
from .test_entries import API, MONDAY, TimetableTestCase


def next_monday() -> date:
    today = date.today()
    return today + timedelta(days=8 - today.isoweekday())  # 1–7 days ahead


class LessonChangeTestCase(TimetableTestCase):
    """Monday: P1 Physics 11 A (Hari, Room 101); P2 Chemistry 11 A (Sita,
    Room 101) and Physics 11 B (Hari, Room 102)."""

    def setUp(self):
        super().setUp()
        self.monday = next_monday()
        self.a_p1 = create_timetable_entry(self.a_physics, self.p1, MONDAY, room=self.r101)
        self.a_p2 = create_timetable_entry(self.a_chemistry, self.p2, MONDAY, room=self.r101)
        self.b_p2 = create_timetable_entry(self.b_physics, self.p2, MONDAY, room=self.r102)

    def change(self, entry, on=None, **data):
        return self.client.post(f"{API}/lesson-changes/", {
            "entry": entry.pk, "date": (on or self.monday).isoformat(), **data,
        }, format="json")

    def day_view(self, **params):
        response = self.client.get(f"{API}/timetable/day/", {"date": self.monday.isoformat(), **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data


class LessonChangeTests(LessonChangeTestCase):
    def test_a_substitute_who_is_free(self):
        response = self.change(self.a_p1, substitute_teacher=self.sita.pk, note="Hari on leave")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["substitute_teacher_name"], self.sita.full_name)
        self.assertEqual(response.data["regular_teacher_name"], self.hari.full_name)

    def test_a_substitute_who_is_teaching_then(self):
        self.assertClash(self.change(self.b_p2, substitute_teacher=self.sita.pk), "teacher")

    def test_a_teacher_freed_by_a_cancellation_can_cover(self):
        self.change(self.a_p2, is_cancelled=True)

        self.assertEqual(self.change(self.b_p2, substitute_teacher=self.sita.pk).status_code, 201)

    def test_a_teacher_covered_by_someone_else_is_free(self):
        gita = create_staff_member(self.lalitpur, employee_number="E-3")
        self.change(self.a_p2, substitute_teacher=gita.pk)

        self.assertEqual(self.change(self.b_p2, substitute_teacher=self.sita.pk).status_code, 201)

    def test_moving_into_a_busy_room(self):
        self.assertClash(self.change(self.a_p2, room=self.r102.pk), "room")

    def test_the_lesson_must_take_place_that_day(self):
        response = self.change(self.a_p1, on=self.monday + timedelta(days=1), substitute_teacher=self.sita.pk)

        self.assertIn("date", response.data["error"]["details"])

    def test_a_change_must_mean_something(self):
        both = self.change(self.a_p1, is_cancelled=True, substitute_teacher=self.sita.pk)
        nothing = self.change(self.a_p1)
        own = self.change(self.a_p1, substitute_teacher=self.hari.pk)

        self.assertEqual((both.status_code, nothing.status_code, own.status_code), (400, 400, 400))

    def test_one_change_per_lesson_per_day(self):
        self.change(self.a_p1, is_cancelled=True)

        self.assertEqual(self.change(self.a_p1, substitute_teacher=self.sita.pk).status_code, 400)

    def test_a_weekly_lesson_cant_take_a_slot_the_teacher_covers(self):
        self.change(self.a_p1, substitute_teacher=self.sita.pk)
        b_chemistry = create_teaching_assignment(self.section_b, self.chemistry, self.sita)

        response = self.schedule(b_chemistry, self.p1, room=self.r102.pk)

        self.assertClash(response, "teacher")
        self.assertEqual(response.data["error"]["details"]["clashes"][0]["date"], self.monday.isoformat())

    def test_staff_view_only(self):
        self.authenticate(user_with_system_role(self.org, "staff", email="t@kmc.test"))

        self.assertEqual(self.change(self.a_p1, is_cancelled=True).status_code, 403)
        self.assertEqual(self.client.get(f"{API}/lesson-changes/").status_code, 200)
        self.assertFalse(LessonChange.objects.exists())


class DayViewTests(LessonChangeTestCase):
    def setUp(self):
        super().setUp()
        self.change(self.a_p1, substitute_teacher=self.sita.pk)
        self.change(self.b_p2, is_cancelled=True)

    def test_a_substitutes_day(self):
        lessons = self.day_view(teacher=self.sita.pk)

        self.assertEqual([(x["subject_name"], x["section_name"]) for x in lessons],
                         [("Physics", "Grade 11 A"), ("Chemistry", "Grade 11 A")])
        self.assertEqual(lessons[0]["regular_teacher"], self.hari.pk)

    def test_the_regular_teachers_day_leaves_out_covered_lessons(self):
        lessons = self.day_view(teacher=self.hari.pk)

        self.assertEqual([(x["section_name"], x["is_cancelled"]) for x in lessons], [("Grade 11 B", True)])

    def test_a_sections_day(self):
        lessons = self.day_view(section=self.section_b.pk)

        self.assertEqual(len(lessons), 1)
        self.assertTrue(lessons[0]["is_cancelled"])

    def test_date_is_required(self):
        self.assertEqual(self.client.get(f"{API}/timetable/day/").status_code, 400)
