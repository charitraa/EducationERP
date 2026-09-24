"""Real life: winter timings. From a date, school starts 30 minutes later."""
from datetime import timedelta

from django.utils import timezone

from tests.factories import create_bell_schedule, create_period, create_timetable_entry

from ..models import Period
from .test_entries import API, MONDAY, TimetableTestCase
from .test_lesson_changes import next_monday


class RetimeTests(TimetableTestCase):
    def setUp(self):
        super().setUp()
        self.monday = next_monday()
        self.last_monday = self.monday - timedelta(days=7)
        self.lesson = create_timetable_entry(self.a_physics, self.p1, MONDAY, room=self.r101)
        self.second = create_timetable_entry(self.a_chemistry, self.p2, MONDAY, room=self.r101)

    def retime(self, *rows, on=None):
        return self.client.post(f"{API}/bell-schedules/{self.day.pk}/retime/", {
            "effective_from": (on or self.monday).isoformat(),
            "periods": [{"period": p.pk, "start_time": s, "end_time": e} for p, s, e in rows],
        }, format="json")

    def times_on(self, day):
        lessons = self.client.get(f"{API}/timetable/day/", {"date": day.isoformat(),
                                                            "section": self.section_a.pk}).data
        return [(x["subject_name"], x["start_time"]) for x in lessons]

    def test_winter_timings_move_lessons_and_keep_the_past(self):
        response = self.retime((self.p1, "10:30", "11:15"), (self.p2, "11:15", "12:00"),
                               (self.lunch, "12:00", "12:30"))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.times_on(self.last_monday), [("Physics", "10:00:00"), ("Chemistry", "10:45:00")])
        self.assertEqual(self.times_on(self.monday), [("Physics", "10:30:00"), ("Chemistry", "11:15:00")])
        # Both versions of Period 1 are listed from today, each with its dates.
        listed = self.client.get(f"{API}/periods/", {"schedule": self.day.pk}).data["results"]
        period_1 = {(p["start_time"], p["valid_from"], p["valid_until"]) for p in listed if p["name"] == "Period 1"}
        self.assertEqual(period_1, {
            ("10:00:00", None, (self.monday - timedelta(days=1)).isoformat()),
            ("10:30:00", self.monday.isoformat(), None),
        })

    def test_periods_cant_overlap_after_the_change(self):
        response = self.retime((self.p1, "10:30", "11:15"))  # runs into Period 2 at 10:45

        self.assertEqual(response.status_code, 409)
        self.assertFalse(Period.objects.filter(valid_from=self.monday).exists())

    def test_a_clash_with_another_shift_stops_everything(self):
        morning = create_bell_schedule(self.lalitpur, name="Morning shift")
        m1 = create_period(morning, "M1", "11:15", "12:00")
        create_timetable_entry(self.b_physics, m1, MONDAY, room=self.r102)  # Hari, 11:15

        response = self.retime((self.p1, "11:15", "12:00"), (self.p2, "12:00", "12:45"),
                               (self.lunch, "12:45", "13:15"))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "timetable_clash")
        self.assertEqual(self.times_on(self.monday)[0], ("Physics", "10:00:00"))

    def test_not_in_the_past(self):
        response = self.retime((self.p1, "09:00", "09:45"), on=timezone.localdate() - timedelta(days=1))

        self.assertEqual(response.status_code, 400)


class LessonsAddedAfterRetimeTests(TimetableTestCase):
    """New bell times are scheduled from next Monday; then a lesson is added
    in the old Period 1. It must follow the bell change like the others."""

    def setUp(self):
        super().setUp()
        self.monday = next_monday()
        self.client.post(f"{API}/bell-schedules/{self.day.pk}/retime/", {
            "effective_from": self.monday.isoformat(),
            "periods": [{"period": self.p1.pk, "start_time": "09:00", "end_time": "09:45"}],
        }, format="json")

    def start_times(self, day, section):
        lessons = self.client.get(f"{API}/timetable/day/", {"date": day.isoformat(), "section": section.pk}).data
        return [x["start_time"] for x in lessons]

    def test_a_lesson_added_by_hand_carries_on_in_the_new_period(self):
        response = self.schedule(self.a_physics, self.p1, valid_from=(self.monday - timedelta(days=7)).isoformat())

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.start_times(self.monday - timedelta(days=7), self.section_a), ["10:00:00"])
        self.assertEqual(self.start_times(self.monday, self.section_a), ["09:00:00"])

    def test_generated_lessons_do_too(self):
        self.a_physics.periods_per_week = 1
        self.a_physics.save()

        response = self.client.post(f"{API}/timetable/generate/", {
            "sections": [self.section_a.pk], "schedule": self.day.pk, "days": [MONDAY], "dry_run": False,
        }, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        on_monday = self.start_times(self.monday, self.section_a)
        self.assertTrue(on_monday and "10:00:00" not in on_monday, on_monday)

    def test_a_lesson_cant_start_in_a_period_before_it_exists(self):
        new_p1 = Period.objects.get(schedule=self.day, name="Period 1", valid_from=self.monday)

        response = self.schedule(self.a_physics, new_p1, valid_from=(self.monday - timedelta(days=7)).isoformat())

        self.assertEqual(response.status_code, 400)
