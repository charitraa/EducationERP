"""Automatic timetable generation."""
from collections import Counter

from tests.factories import (
    create_bell_schedule,
    create_period,
    create_staff_member,
    create_teaching_assignment,
    create_timetable_entry,
)

from ..models import TimetableEntry
from .test_entries import API, MONDAY, TimetableTestCase

GENERATE = f"{API}/timetable/generate/"
DAYS = [1, 2, 3]  # 3 days × 2 teaching periods = 6 slots a week


class GenerateTests(TimetableTestCase):
    def setUp(self):
        super().setUp()
        for assignment, weekly in ((self.a_physics, 3), (self.a_chemistry, 2), (self.b_physics, 3)):
            assignment.periods_per_week = weekly
            assignment.save()

    def generate(self, sections=None, **extra):
        return self.client.post(GENERATE, {
            "sections": [s.pk for s in sections or (self.section_a, self.section_b)],
            "schedule": self.day.pk, "days": DAYS, **extra,
        }, format="json")

    def slots(self, lessons):
        return [(x["day_of_week"], x["period"]) for x in lessons]

    def test_dry_run_plans_without_saving(self):
        response = self.generate()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["dry_run"])
        self.assertEqual(len(response.data["lessons"]), 8)
        self.assertEqual(response.data["unplaced"], [])
        self.assertFalse(TimetableEntry.objects.exists())

    def test_the_plan_has_no_clashes_and_spreads_subjects(self):
        lessons = self.generate().data["lessons"]

        hari = [x for x in lessons if x["teacher_name"] == self.hari.full_name]
        self.assertEqual(len(set(self.slots(hari))), len(hari))
        for section in (self.section_a, self.section_b):
            mine = [x for x in lessons if x["section"] == section.pk]
            self.assertEqual(len(set(self.slots(mine))), len(mine))
        physics_a = [x for x in lessons if x["teaching_assignment"] == self.a_physics.pk]
        self.assertEqual(sorted(x["day_of_week"] for x in physics_a), DAYS)
        # Lessons go in the section's home room.
        self.assertEqual({x["room"] for x in lessons if x["section"] == self.section_a.pk}, {self.r101.pk})

    def test_saving_and_generating_again_only_fills_gaps(self):
        saved = self.generate(dry_run=False)
        again = self.generate(dry_run=False)

        self.assertEqual(saved.status_code, 201, saved.data)
        self.assertEqual(saved.data["created"], 8)
        self.assertEqual(TimetableEntry.objects.count(), 8)
        self.assertEqual((again.status_code, again.data["lessons"]), (200, []))

    def test_existing_lessons_count(self):
        create_timetable_entry(self.a_physics, self.p1, MONDAY, room=self.r101)

        lessons = self.generate().data["lessons"]

        self.assertEqual(Counter(x["teaching_assignment"] for x in lessons)[self.a_physics.pk], 2)
        self.assertNotIn((MONDAY, self.p1.pk), self.slots(
            [x for x in lessons if x["section"] == self.section_a.pk]
        ))

    def test_what_doesnt_fit_is_reported(self):
        self.b_physics.periods_per_week = 4  # Hari would need 7 of 6 slots
        self.b_physics.save()

        response = self.generate()

        self.assertEqual(len(response.data["unplaced"]), 1)
        self.assertEqual(response.data["unplaced"][0]["missing"], 1)

    def test_electives_run_in_parallel_in_different_rooms(self):
        computer = create_teaching_assignment(self.section_a, self.computer,
                                              create_staff_member(self.lalitpur, employee_number="E-3"))
        biology = create_teaching_assignment(self.section_a, self.biology,
                                             create_staff_member(self.lalitpur, employee_number="E-4"))
        for assignment in (computer, biology):
            assignment.periods_per_week = 1
            assignment.save()
        self.a_physics.periods_per_week = 2
        self.a_physics.save()
        # Both electives would also fit one after the other; the generator
        # prefers running them side by side, as +2 colleges do.

        lessons = self.generate(sections=[self.section_a]).data["lessons"]

        by_ta = {x["teaching_assignment"]: x for x in lessons}
        self.assertEqual(self.slots([by_ta[computer.pk]]), self.slots([by_ta[biology.pk]]))
        self.assertNotEqual(by_ta[computer.pk]["room"], by_ta[biology.pk]["room"])

    def test_teacher_busy_at_another_campus_is_respected(self):
        far_section = self.bhaktapur.sections.create(
            organization=self.org, academic_year=self.year, program=self.program, level=11, name="A")
        far_period = create_period(create_bell_schedule(self.bhaktapur), "P", "10:00", "10:45")
        create_timetable_entry(create_teaching_assignment(far_section, self.physics, self.hari), far_period, MONDAY)

        lessons = self.generate().data["lessons"]

        hari = [x for x in lessons if x["teacher_name"] == self.hari.full_name]
        self.assertNotIn((MONDAY, self.p1.pk), self.slots(hari))

    def test_validation(self):
        far_schedule = create_bell_schedule(self.bhaktapur)

        self.assertEqual(self.generate(days=[1, 1]).status_code, 400)
        self.assertEqual(self.generate(days=[8]).status_code, 400)
        self.assertEqual(self.generate(schedule=far_schedule.pk).status_code, 400)

    def test_campus_scope(self):
        far_schedule = create_bell_schedule(self.bhaktapur)
        far_section = self.bhaktapur.sections.create(
            organization=self.org, academic_year=self.year, program=self.program, level=11, name="A")

        response = self.generate(sections=[far_section], schedule=far_schedule.pk)

        self.assertEqual(response.status_code, 403)


class GenerateOneYearTests(TimetableTestCase):
    def test_sections_of_different_years_are_refused(self):
        from datetime import timedelta

        from tests.factories import create_academic_year, create_section

        other_year = create_academic_year(self.org, name="2083/84", start=self.year.end_date + timedelta(days=1))
        far = create_section(self.lalitpur, self.program, other_year, level=12, name="A")

        response = self.client.post(GENERATE, {"sections": [self.section_a.pk, far.pk], "schedule": self.day.pk,
                                               "days": [1]}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("one academic year", str(response.data))
