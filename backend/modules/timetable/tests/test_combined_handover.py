"""Combined classes and handing lessons over to another teacher."""
import uuid

from tests.factories import (
    create_staff_member,
    create_teaching_assignment,
    create_timetable_entry,
    user_with_system_role,
)

from modules.academics.models import TeachingAssignment

from ..models import TimetableEntry
from .test_entries import API, MONDAY, TUESDAY, TimetableTestCase


class CombinedClassTests(TimetableTestCase):
    """Hari teaches Physics to 11 A and 11 B together, in Room 101."""

    def setUp(self):
        super().setUp()
        self.lesson = self.schedule(self.a_physics, self.p1, room=self.r101.pk).data["id"]

    def combine(self, assignment, **extra):
        return self.client.post(f"{API}/timetable/", {
            "teaching_assignment": assignment.pk, "combine_with": self.lesson, **extra,
        })

    def test_join_a_combined_class(self):
        response = self.combine(self.b_physics)

        self.assertEqual(response.status_code, 201, response.data)
        a, b = TimetableEntry.objects.get(pk=self.lesson), TimetableEntry.objects.get(pk=response.data["id"])
        self.assertIsNotNone(a.combined_group)
        self.assertEqual(a.combined_group, b.combined_group)
        self.assertEqual((b.day_of_week, b.period_id, b.room_id), (MONDAY, self.p1.pk, self.r101.pk))

    def test_one_teacher_and_one_subject(self):
        b_chemistry = create_teaching_assignment(self.section_b, self.chemistry, self.hari)
        b_physics_sita = create_teaching_assignment(self.section_b, self.physics, self.sita)

        self.assertEqual(self.combine(b_chemistry).status_code, 400)
        self.assertEqual(self.combine(b_physics_sita).status_code, 400)

    def test_the_slot_comes_from_the_class(self):
        response = self.combine(self.b_physics, period=self.p2.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn("period", response.data["error"]["details"])

    def test_the_joining_section_must_be_free(self):
        b_chemistry = create_teaching_assignment(self.section_b, self.chemistry, self.sita)
        create_timetable_entry(b_chemistry, self.p1, MONDAY, room=self.r102)

        self.assertClash(self.combine(self.b_physics), "section")

    def test_the_class_moves_together(self):
        other = self.combine(self.b_physics).data["id"]

        response = self.client.patch(f"{API}/timetable/{self.lesson}/", {"period": self.p2.pk, "day_of_week": TUESDAY})

        self.assertEqual(response.status_code, 200, response.data)
        moved = TimetableEntry.objects.get(pk=other)
        self.assertEqual((moved.day_of_week, moved.period_id), (TUESDAY, self.p2.pk))

    def test_a_move_that_clashes_for_any_section_moves_nothing(self):
        other = self.combine(self.b_physics).data["id"]
        b_chemistry = create_teaching_assignment(self.section_b, self.chemistry, self.sita)
        create_timetable_entry(b_chemistry, self.p2, MONDAY, room=self.r102)

        response = self.client.patch(f"{API}/timetable/{self.lesson}/", {"period": self.p2.pk})

        self.assertClash(response, "section")
        self.assertEqual(TimetableEntry.objects.get(pk=other).period_id, self.p1.pk)
        self.assertEqual(TimetableEntry.objects.get(pk=self.lesson).period_id, self.p1.pk)

    def test_leaving_the_class(self):
        other = self.combine(self.b_physics).data["id"]

        self.client.delete(f"{API}/timetable/{other}/")

        self.assertIsNone(TimetableEntry.objects.get(pk=self.lesson).combined_group)

    def test_teacher_of_a_combined_class_changes_through_hand_over(self):
        self.combine(self.b_physics)
        replacement = create_teaching_assignment(self.section_a, self.physics, self.sita)

        response = self.client.patch(f"{API}/timetable/{self.lesson}/", {"teaching_assignment": replacement.pk})

        self.assertEqual(response.status_code, 400)
        self.assertIn("hand-over", str(response.data))


class HandOverTests(TimetableTestCase):
    def setUp(self):
        super().setUp()
        self.gita = create_staff_member(self.lalitpur, employee_number="E-3", first_name="Gita")
        self.a_lesson = create_timetable_entry(self.a_physics, self.p1, MONDAY, room=self.r101)
        self.b_lesson = create_timetable_entry(self.b_physics, self.p2, MONDAY, room=self.r102)

    def hand_over(self, assignments, teacher):
        return self.client.post(f"{API}/timetable/hand-over/", {
            "teaching_assignments": [a.pk for a in assignments], "teacher": teacher.pk,
        }, format="json")

    def test_all_lessons_move_to_the_new_teacher(self):
        response = self.hand_over([self.a_physics, self.b_physics], self.gita)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({a["teacher"] for a in response.data}, {self.gita.pk})
        self.a_lesson.refresh_from_db()
        self.assertEqual(self.a_lesson.teaching_assignment.teacher_id, self.gita.pk)
        # The old assignment stays, without lessons, as a record.
        self.assertTrue(TeachingAssignment.objects.filter(pk=self.a_physics.pk).exists())
        self.assertFalse(self.a_physics.timetable_entries.exists())

    def test_a_clash_moves_nothing(self):
        gita_elsewhere = create_teaching_assignment(self.section_b, self.chemistry, self.gita)
        create_timetable_entry(gita_elsewhere, self.p1, MONDAY, room=self.r102)

        response = self.hand_over([self.a_physics, self.b_physics], self.gita)

        self.assertClash(response, "teacher")
        self.a_lesson.refresh_from_db()
        self.b_lesson.refresh_from_db()
        self.assertEqual(self.a_lesson.teaching_assignment_id, self.a_physics.pk)
        self.assertEqual(self.b_lesson.teaching_assignment_id, self.b_physics.pk)

    def test_lessons_at_the_same_time_cant_all_go_to_one_teacher(self):
        create_timetable_entry(self.a_chemistry, self.p2, MONDAY, room=self.r101)  # Sita, same time as B physics

        response = self.hand_over([self.a_chemistry, self.b_physics], self.gita)

        self.assertClash(response, "teacher")

    def test_a_combined_class_must_be_handed_over_whole(self):
        key = uuid.uuid4()
        b_joined = create_timetable_entry(self.b_physics, self.p1, MONDAY, room=self.r101)
        TimetableEntry.objects.filter(pk__in=[self.a_lesson.pk, b_joined.pk]).update(combined_group=key)

        partial = self.hand_over([self.a_physics], self.gita)
        whole = self.hand_over([self.a_physics, self.b_physics], self.gita)

        self.assertEqual(partial.status_code, 409)
        self.assertEqual(partial.data["error"]["code"], "combined_class")
        self.assertEqual(whole.status_code, 200, whole.data)

    def test_refused_when_nothing_would_change_or_without_permission(self):
        self.assertEqual(self.hand_over([self.a_physics], self.hari).status_code, 400)
        self.authenticate(user_with_system_role(self.org, "staff", email="t@kmc.test"))
        self.assertEqual(self.hand_over([self.a_physics], self.gita).status_code, 403)
