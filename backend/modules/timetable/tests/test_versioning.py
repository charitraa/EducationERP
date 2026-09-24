"""Real life: the timetable changes mid-semester, and the past stays true.

September: Monday 10:00 Physics for 11 A in Room 101. From next Monday it
moves to Room 102. Attendance taken in September must still read Room 101.
"""
from datetime import timedelta

from django.utils import timezone

from tests.factories import create_timetable_entry

from ..models import LessonChange, TimetableEntry
from .test_entries import API, MONDAY, TimetableTestCase
from .test_lesson_changes import next_monday


class VersioningTests(TimetableTestCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.last_monday = next_monday() - timedelta(days=7)
        self.monday = next_monday()
        # Running since the start of the year.
        self.lesson = create_timetable_entry(self.a_physics, self.p1, MONDAY, room=self.r101)

    def on(self, day, **params):
        response = self.client.get(f"{API}/timetable/day/", {"date": day.isoformat(), **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_a_room_change_from_a_date_keeps_the_past(self):
        response = self.client.patch(f"{API}/timetable/{self.lesson.pk}/", {
            "room": self.r102.pk, "effective_from": self.monday.isoformat()})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotEqual(response.data["id"], self.lesson.pk)  # a new version
        self.assertEqual(self.on(self.last_monday)[0]["room"], self.r101.pk)
        self.assertEqual(self.on(self.monday)[0]["room"], self.r102.pk)
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.valid_until, self.monday - timedelta(days=1))

    def test_a_lesson_that_hasnt_started_is_edited_in_place(self):
        future = self.schedule(self.b_physics, self.p2, valid_from=self.monday.isoformat()).data["id"]

        response = self.client.patch(f"{API}/timetable/{future}/", {"room": self.r101.pk})

        self.assertEqual(response.data["id"], future)
        self.assertEqual(TimetableEntry.objects.filter(teaching_assignment=self.b_physics).count(), 1)

    def test_removing_a_lesson_that_ran_ends_it(self):
        response = self.client.delete(f"{API}/timetable/{self.lesson.pk}/")

        self.assertEqual(response.status_code, 204)
        self.lesson.refresh_from_db()
        self.assertIsNone(self.lesson.deleted_at)
        self.assertEqual(self.lesson.valid_until, self.today - timedelta(days=1))
        self.assertEqual(len(self.on(self.last_monday)), 1)
        self.assertEqual(self.on(self.monday), [])

    def test_the_freed_slot_is_free_only_from_the_change(self):
        self.client.patch(f"{API}/timetable/{self.lesson.pk}/", {
            "period": self.p2.pk, "effective_from": self.monday.isoformat()})

        before = self.schedule(self.a_chemistry, self.p1, valid_from=self.last_monday.isoformat())
        after = self.schedule(self.a_chemistry, self.p1, valid_from=self.monday.isoformat())

        self.assertClash(before, "section")
        self.assertEqual(after.status_code, 201, after.data)

    def test_the_list_shows_the_timetable_from_today(self):
        self.client.patch(f"{API}/timetable/{self.lesson.pk}/", {"room": self.r102.pk})

        current = self.client.get(f"{API}/timetable/", {"section": self.section_a.pk}).data["results"]
        everything = self.client.get(f"{API}/timetable/", {"section": self.section_a.pk,
                                                            "include_ended": "true"}).data["results"]

        self.assertEqual([e["room"] for e in current], [self.r102.pk])
        self.assertEqual(len(everything), 2)

    def test_a_lesson_added_mid_year_runs_from_today(self):
        response = self.schedule(self.b_physics, self.p2)

        self.assertEqual(response.data["valid_from"], self.today.isoformat())
        self.assertEqual(self.on(self.last_monday, section=self.section_b.pk), [])

    def test_planned_substitutes_follow_the_new_version(self):
        change = LessonChange.objects.create(organization=self.org, entry=self.lesson, date=self.monday,
                                             substitute_teacher=self.sita)

        new = self.client.patch(f"{API}/timetable/{self.lesson.pk}/", {"room": self.r102.pk}).data["id"]

        change.refresh_from_db()
        self.assertEqual(change.entry_id, new)

    def test_moving_to_another_day_with_planned_substitutes_is_refused(self):
        LessonChange.objects.create(organization=self.org, entry=self.lesson, date=self.monday,
                                    substitute_teacher=self.sita)

        response = self.client.patch(f"{API}/timetable/{self.lesson.pk}/", {"day_of_week": 2})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "planned_changes")

    def test_a_combined_class_changes_together_from_a_date(self):
        b = create_timetable_entry(self.b_physics, self.p1, MONDAY, room=self.r101)
        TimetableEntry.objects.filter(pk__in=[self.lesson.pk, b.pk]).update(combined_group="7c3e6b5e-0000-4000-8000-000000000001")

        response = self.client.patch(f"{API}/timetable/{self.lesson.pk}/", {
            "period": self.p2.pk, "effective_from": self.monday.isoformat()})

        self.assertEqual(response.status_code, 200, response.data)
        after = self.on(self.monday)
        self.assertEqual({x["section_name"] for x in after}, {"Grade 11 A", "Grade 11 B"})
        self.assertEqual({x["period"] for x in after}, {self.p2.pk})
        self.assertEqual({x["period"] for x in self.on(self.last_monday)}, {self.p1.pk})
