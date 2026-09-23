"""Bell schedules and their periods."""
from django.db import IntegrityError, transaction

from tests.base import APITestCaseBase
from tests.factories import (
    add_to_curriculum,
    create_academic_year,
    create_bell_schedule,
    create_campus,
    create_organization,
    create_period,
    create_program,
    create_section,
    create_staff_member,
    create_subject,
    create_teaching_assignment,
    create_timetable_entry,
    user_with_system_role,
)

from ..models import Period

API = "/api/v1"


class BellScheduleTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.lalitpur = create_campus(self.org, code="lalitpur", name="Lalitpur")
        self.bhaktapur = create_campus(self.org, code="bhaktapur", name="Bhaktapur")
        self.schedule = create_bell_schedule(self.lalitpur)
        self.authenticate(user_with_system_role(self.org, "campus-admin", email="ram@kmc.test",
                                                campus=self.lalitpur))

    def period(self, name, start, end, **extra):
        return self.client.post(f"{API}/periods/", {
            "schedule": self.schedule.pk, "name": name, "start_time": start, "end_time": end, **extra,
        })

    def scheduled_period(self):
        """A period with a lesson in it."""
        period = create_period(self.schedule, "Period 1", "10:00", "10:45")
        program = create_program(self.org)
        subject = create_subject(self.org)
        add_to_curriculum(program, subject, level=11)
        section = create_section(self.lalitpur, program, create_academic_year(self.org))
        assignment = create_teaching_assignment(section, subject, create_staff_member(self.lalitpur))
        create_timetable_entry(assignment, period)
        return period


class BellScheduleTests(BellScheduleTestCase):
    def test_names_unique_per_campus(self):
        same = self.client.post(f"{API}/bell-schedules/", {"campus": self.lalitpur.pk, "name": "Day shift"})
        other = self.client.post(f"{API}/bell-schedules/", {"campus": self.lalitpur.pk, "name": "Morning shift"})

        self.assertEqual(same.status_code, 400)
        self.assertEqual(other.status_code, 201, other.data)

    def test_campus_admin_cannot_create_at_another_campus(self):
        response = self.client.post(f"{API}/bell-schedules/", {"campus": self.bhaktapur.pk, "name": "Day"})

        self.assertEqual(response.status_code, 403)

    def test_cannot_move_campus(self):
        self.authenticate(user_with_system_role(self.org, "org-admin", email="p@kmc.test"))

        response = self.client.patch(f"{API}/bell-schedules/{self.schedule.pk}/", {"campus": self.bhaktapur.pk})

        self.assertEqual(response.status_code, 400)

    def test_deleting_takes_its_periods_along(self):
        period = create_period(self.schedule, "Period 1", "10:00", "10:45")

        self.assertEqual(self.client.delete(f"{API}/bell-schedules/{self.schedule.pk}/").status_code, 204)
        self.assertFalse(Period.objects.filter(pk=period.pk).exists())

    def test_a_schedule_with_lessons_cannot_be_deleted(self):
        self.scheduled_period()

        self.assertEqual(self.client.delete(f"{API}/bell-schedules/{self.schedule.pk}/").status_code, 409)


class PeriodTests(BellScheduleTestCase):
    def test_create_a_day_of_periods(self):
        responses = [
            self.period("Period 1", "10:00", "10:45"),
            self.period("Period 2", "10:45", "11:30"),
            self.period("Lunch", "11:30", "12:00", is_break=True),
        ]

        self.assertEqual([r.status_code for r in responses], [201, 201, 201])
        listed = self.client.get(f"{API}/periods/", {"schedule": self.schedule.pk})
        self.assertEqual([p["name"] for p in listed.data["results"]], ["Period 1", "Period 2", "Lunch"])

    def test_end_must_be_after_start_and_backed_by_the_database(self):
        self.assertEqual(self.period("Bad", "10:45", "10:00").status_code, 400)
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_period(self.schedule, "Bad", "10:45", "10:00")

    def test_periods_of_one_schedule_cannot_overlap(self):
        self.period("Period 1", "10:00", "10:45")

        overlapping = self.period("Period 2", "10:30", "11:15")
        other_schedule = create_bell_schedule(self.lalitpur, name="Morning shift")
        elsewhere = self.client.post(f"{API}/periods/", {
            "schedule": other_schedule.pk, "name": "Period 1", "start_time": "10:30", "end_time": "11:15",
        })

        self.assertEqual(overlapping.status_code, 400)
        self.assertIn("Overlaps Period 1", str(overlapping.data))
        self.assertEqual(elsewhere.status_code, 201)

    def test_times_are_locked_once_lessons_are_scheduled(self):
        period = self.scheduled_period()

        moved = self.client.patch(f"{API}/periods/{period.pk}/", {"start_time": "09:30"})
        break_ = self.client.patch(f"{API}/periods/{period.pk}/", {"is_break": True})
        renamed = self.client.patch(f"{API}/periods/{period.pk}/", {"name": "First period"})

        self.assertEqual((moved.status_code, break_.status_code, renamed.status_code), (400, 400, 200))

    def test_a_period_with_lessons_cannot_be_deleted(self):
        period = self.scheduled_period()

        self.assertEqual(self.client.delete(f"{API}/periods/{period.pk}/").status_code, 409)

    def test_periods_of_another_campus_are_invisible(self):
        far = create_period(create_bell_schedule(self.bhaktapur), "Period 1", "10:00", "10:45")

        self.assertEqual(self.client.get(f"{API}/periods/{far.pk}/").status_code, 404)
        self.assertEqual(self.period("P", "07:00", "07:45").status_code, 201)
        response = self.client.post(f"{API}/periods/", {
            "schedule": far.schedule_id, "name": "P", "start_time": "07:00", "end_time": "07:45",
        })
        self.assertEqual(response.status_code, 403)
