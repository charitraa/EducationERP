"""The weekly timetable: validation, clash detection, scoping, and the
guards academics keeps for records the timetable uses."""
from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext

from tests.base import APITestCaseBase
from tests.factories import (
    add_to_curriculum,
    create_academic_year,
    create_bell_schedule,
    create_campus,
    create_organization,
    create_period,
    create_program,
    create_room,
    create_section,
    create_staff_member,
    create_subject,
    create_teaching_assignment,
    create_term,
    create_timetable_entry,
    user_with_system_role,
)

from ..models import TimetableEntry, Weekday

API = "/api/v1"
MONDAY, TUESDAY = Weekday.MONDAY, Weekday.TUESDAY


class TimetableTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.lalitpur = create_campus(self.org, code="lalitpur", name="Lalitpur")
        self.bhaktapur = create_campus(self.org, code="bhaktapur", name="Bhaktapur")
        self.year = create_academic_year(self.org)
        self.program = create_program(self.org)  # Grade 11–12

        self.physics = create_subject(self.org, code="physics", name="Physics")
        self.chemistry = create_subject(self.org, code="chemistry", name="Chemistry")
        self.computer = create_subject(self.org, code="computer", name="Computer Science")
        self.biology = create_subject(self.org, code="biology", name="Biology")
        for subject in (self.physics, self.chemistry):
            add_to_curriculum(self.program, subject, level=11)
        for subject in (self.computer, self.biology):
            add_to_curriculum(self.program, subject, level=11, is_elective=True)

        self.r101 = create_room(self.lalitpur, code="r101", name="Room 101")
        self.r102 = create_room(self.lalitpur, code="r102", name="Room 102")
        self.section_a = create_section(self.lalitpur, self.program, self.year, name="A", home_room=self.r101)
        self.section_b = create_section(self.lalitpur, self.program, self.year, name="B", home_room=self.r102)

        self.hari = create_staff_member(self.lalitpur, employee_number="E-1", first_name="Hari")
        self.sita = create_staff_member(self.lalitpur, employee_number="E-2", first_name="Sita")

        self.day = create_bell_schedule(self.lalitpur, name="Day shift")
        self.p1 = create_period(self.day, "Period 1", "10:00", "10:45")
        self.p2 = create_period(self.day, "Period 2", "10:45", "11:30")
        self.lunch = create_period(self.day, "Lunch", "11:30", "12:00", is_break=True)

        self.a_physics = create_teaching_assignment(self.section_a, self.physics, self.hari)
        self.a_chemistry = create_teaching_assignment(self.section_a, self.chemistry, self.sita)
        self.b_physics = create_teaching_assignment(self.section_b, self.physics, self.hari)

        self.admin = user_with_system_role(self.org, "campus-admin", email="ram@kmc.test", campus=self.lalitpur)
        self.authenticate(self.admin)

    def schedule(self, assignment, period, day=MONDAY, **extra):
        return self.client.post(f"{API}/timetable/", {
            "teaching_assignment": assignment.pk, "period": period.pk, "day_of_week": day, **extra,
        })

    def assertClash(self, response, kind):
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["error"]["code"], "timetable_clash")
        kinds = {clash["kind"] for clash in response.data["error"]["details"]["clashes"]}
        self.assertIn(kind, kinds)


class ScheduleTests(TimetableTestCase):
    def test_schedule_a_lesson(self):
        response = self.schedule(self.a_physics, self.p1)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["day_name"], "Monday")
        self.assertEqual(response.data["section_name"], "Grade 11 A")
        self.assertEqual(response.data["subject_name"], "Physics")
        self.assertEqual(response.data["start_time"], "10:00:00")
        # Left out, the room is the section's home room.
        self.assertEqual(response.data["room"], self.r101.pk)

    def test_room_can_be_chosen_or_left_empty(self):
        lab = self.schedule(self.a_physics, self.p1, room=self.r102.pk)
        none = self.client.post(f"{API}/timetable/", {
            "teaching_assignment": self.a_chemistry.pk, "period": self.p2.pk, "day_of_week": MONDAY,
            "room": None,
        }, format="json")

        self.assertEqual(lab.data["room"], self.r102.pk)
        self.assertIsNone(none.data["room"])

    def test_no_lessons_in_a_break(self):
        response = self.schedule(self.a_physics, self.lunch)

        self.assertEqual(response.status_code, 400)
        self.assertIn("period", response.data["error"]["details"])

    def test_period_and_room_must_be_at_the_sections_campus(self):
        far_period = create_period(create_bell_schedule(self.bhaktapur), "Period 1", "10:00", "10:45")
        far_room = create_room(self.bhaktapur)

        period = self.schedule(self.a_physics, far_period)
        room = self.schedule(self.a_physics, self.p1, room=far_room.pk)

        self.assertIn("period", period.data["error"]["details"])
        self.assertIn("room", room.data["error"]["details"])

    def test_term_must_belong_to_the_sections_year(self):
        other_year = create_academic_year(
            self.org, name="2083/84", start=self.year.end_date + timedelta(days=1)
        )

        response = self.schedule(self.a_physics, self.p1, term=create_term(other_year).pk)

        self.assertIn("term", response.data["error"]["details"])

    def test_a_teacher_who_left_cannot_be_scheduled(self):
        gone = create_staff_member(self.lalitpur, employee_number="E-9", status="left", left_on="2025-01-01")
        assignment = create_teaching_assignment(self.section_a, self.physics, gone)

        self.assertEqual(self.schedule(assignment, self.p1).status_code, 400)

    def test_exact_duplicate_is_rejected_and_backed_by_the_database(self):
        self.schedule(self.a_physics, self.p1)

        self.assertEqual(self.schedule(self.a_physics, self.p1).status_code, 400)
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_timetable_entry(self.a_physics, self.p1, MONDAY)


class ClashTests(TimetableTestCase):
    def test_teacher_cannot_be_in_two_sections_at_once(self):
        self.schedule(self.a_physics, self.p1)

        response = self.schedule(self.b_physics, self.p1)

        self.assertClash(response, "teacher")
        clash = response.data["error"]["details"]["clashes"][0]
        self.assertEqual((clash["section"], clash["subject"]), ("Grade 11 A", "Physics"))

    def test_teacher_clash_is_found_across_campuses(self):
        far_section = create_section(self.bhaktapur, self.program, self.year, name="A")
        far_period = create_period(create_bell_schedule(self.bhaktapur), "Period 3", "10:30", "11:15")
        create_timetable_entry(create_teaching_assignment(far_section, self.physics, self.hari), far_period)

        response = self.schedule(self.a_physics, self.p1)

        self.assertClash(response, "teacher")
        # The Lalitpur admin learns when and where, not Bhaktapur's timetable.
        clash = response.data["error"]["details"]["clashes"][0]
        self.assertEqual((clash["campus"], clash["start_time"]), ("Bhaktapur", "10:30"))
        self.assertIsNone(clash["section"])
        self.assertIsNone(clash["subject"])

    def test_room_cannot_hold_two_lessons_at_once(self):
        self.schedule(self.a_physics, self.p1)

        response = self.schedule(self.a_chemistry, self.p1, room=self.r101.pk, day=MONDAY)

        self.assertClash(response, "room")

    def test_section_cannot_have_two_lessons_at_once(self):
        self.schedule(self.a_physics, self.p1, room=self.r101.pk)

        response = self.schedule(self.a_chemistry, self.p1, room=self.r102.pk)

        self.assertClash(response, "section")

    def test_electives_run_in_parallel(self):
        computer = create_teaching_assignment(self.section_a, self.computer, self.hari)
        biology = create_teaching_assignment(self.section_a, self.biology, self.sita)

        first = self.schedule(computer, self.p1, room=self.r101.pk)
        second = self.schedule(biology, self.p1, room=self.r102.pk)
        # ...but not alongside a compulsory subject.
        chemistry = self.schedule(self.a_chemistry, self.p1, room=None)

        self.assertEqual((first.status_code, second.status_code), (201, 201), second.data)
        self.assertClash(chemistry, "section")

    def test_overlap_is_by_clock_time_across_bell_schedules(self):
        morning = create_bell_schedule(self.lalitpur, name="Morning shift")
        m1 = create_period(morning, "M1", "10:30", "11:15")
        self.schedule(self.a_physics, self.p1)

        self.assertClash(self.schedule(self.b_physics, m1), "teacher")

    def test_back_to_back_and_other_days_are_fine(self):
        self.schedule(self.a_physics, self.p1)

        next_period = self.schedule(self.b_physics, self.p2)
        next_day = self.schedule(self.b_physics, self.p1, day=TUESDAY)

        self.assertEqual((next_period.status_code, next_day.status_code), (201, 201))

    def test_terms(self):
        first, second = create_term(self.year, 1), create_term(
            self.year, 2, start=self.year.start_date + timedelta(days=200), end=self.year.end_date
        )
        self.schedule(self.a_physics, self.p1, term=first.pk)

        other_term = self.schedule(self.b_physics, self.p1, term=second.pk)
        all_year = self.schedule(self.b_physics, self.p1, day=MONDAY)

        self.assertEqual(other_term.status_code, 201, other_term.data)
        # An all-year lesson meets during every term.
        self.assertClash(all_year, "teacher")

    def test_other_academic_years_never_clash(self):
        next_year = create_academic_year(self.org, name="2083/84", start=self.year.end_date + timedelta(days=1))
        next_section = create_section(self.lalitpur, self.program, next_year, name="A")
        create_timetable_entry(create_teaching_assignment(next_section, self.physics, self.hari), self.p1)

        self.assertEqual(self.schedule(self.a_physics, self.p1).status_code, 201)

    def test_deleted_lessons_free_their_slot(self):
        entry = self.schedule(self.a_physics, self.p1).data["id"]
        self.assertEqual(self.client.delete(f"{API}/timetable/{entry}/").status_code, 204)

        self.assertEqual(self.schedule(self.b_physics, self.p1).status_code, 201)

    def test_moving_a_lesson_is_checked_too(self):
        self.schedule(self.a_physics, self.p1)
        entry = self.schedule(self.b_physics, self.p2).data["id"]

        into_clash = self.client.patch(f"{API}/timetable/{entry}/", {"period": self.p1.pk})
        unchanged = self.client.patch(f"{API}/timetable/{entry}/", {"period": self.p2.pk})

        self.assertClash(into_clash, "teacher")
        self.assertEqual(unchanged.status_code, 200)

    def test_handing_lessons_to_another_teacher(self):
        entry = self.schedule(self.a_physics, self.p1).data["id"]
        replacement = create_teaching_assignment(self.section_a, self.physics, self.sita)
        other_section = self.client.patch(f"{API}/timetable/{entry}/", {"teaching_assignment": self.b_physics.pk})

        moved = self.client.patch(f"{API}/timetable/{entry}/", {"teaching_assignment": replacement.pk})

        self.assertEqual(other_section.status_code, 400)
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertEqual(moved.data["teacher_name"], self.sita.full_name)


class ReadTests(TimetableTestCase):
    def setUp(self):
        super().setUp()
        self.first_term = create_term(self.year, 1)
        create_timetable_entry(self.a_physics, self.p1, MONDAY, room=self.r101)
        create_timetable_entry(self.a_chemistry, self.p2, MONDAY, room=self.r101, term=self.first_term)
        create_timetable_entry(self.b_physics, self.p2, TUESDAY, room=self.r102)

    def ids(self, **params):
        response = self.client.get(f"{API}/timetable/", params)
        self.assertEqual(response.status_code, 200, response.data)
        return [(e["subject_name"], e["section_name"], e["day_of_week"]) for e in response.data["results"]]

    def test_a_sections_and_a_teachers_week(self):
        self.assertEqual(self.ids(section=self.section_a.pk),
                         [("Physics", "Grade 11 A", 1), ("Chemistry", "Grade 11 A", 1)])
        self.assertEqual(self.ids(teacher=self.hari.pk),
                         [("Physics", "Grade 11 A", 1), ("Physics", "Grade 11 B", 2)])
        self.assertEqual(self.ids(room=self.r102.pk), [("Physics", "Grade 11 B", 2)])

    def test_lessons_on_a_date(self):
        in_term = self.first_term.start_date + timedelta(days=(7 - self.first_term.start_date.isoweekday() + 1) % 7)
        after_term = in_term + timedelta(weeks=((self.first_term.end_date - in_term).days // 7) + 1)
        self.assertEqual(in_term.isoweekday(), 1)

        self.assertEqual(self.ids(date=in_term.isoformat()),
                         [("Physics", "Grade 11 A", 1), ("Chemistry", "Grade 11 A", 1)])
        self.assertEqual(self.ids(date=after_term.isoformat()), [("Physics", "Grade 11 A", 1)])
        outside_year = self.year.end_date + timedelta(days=7 - self.year.end_date.isoweekday() + 1)
        self.assertEqual(self.ids(date=outside_year.isoformat()), [])

    def test_list_query_count_does_not_grow_with_rows(self):
        self.client.get(f"{API}/timetable/")  # warm per-user caches
        with CaptureQueriesContext(connection) as few:
            self.client.get(f"{API}/timetable/")
        for day in (3, 4, 5, 6):
            create_timetable_entry(self.a_physics, self.p1, day)
        with CaptureQueriesContext(connection) as many:
            self.client.get(f"{API}/timetable/")

        self.assertEqual(len(few), len(many))


class ScopeTests(TimetableTestCase):
    def test_campus_admin_sees_and_writes_only_own_campus(self):
        far_section = create_section(self.bhaktapur, self.program, self.year, name="A")
        far_teacher = create_staff_member(self.bhaktapur, employee_number="E-7")
        far_assignment = create_teaching_assignment(far_section, self.physics, far_teacher)
        far_period = create_period(create_bell_schedule(self.bhaktapur), "Period 1", "10:00", "10:45")
        far_entry = create_timetable_entry(far_assignment, far_period)

        self.assertEqual(self.client.get(f"{API}/timetable/{far_entry.pk}/").status_code, 404)
        self.assertEqual(self.schedule(far_assignment, far_period, day=TUESDAY).status_code, 403)
        self.assertEqual(self.client.get(f"{API}/timetable/").data["count"], 0)

    def test_staff_can_view_but_not_change(self):
        self.schedule(self.a_physics, self.p1)
        self.authenticate(user_with_system_role(self.org, "staff", email="t@kmc.test"))

        self.assertEqual(self.client.get(f"{API}/timetable/").status_code, 200)
        self.assertEqual(self.schedule(self.a_chemistry, self.p2).status_code, 403)

    def test_other_organizations_records_are_unknown(self):
        foreign_org = create_organization(code="other")
        foreign_campus = create_campus(foreign_org, code="main")
        foreign_period = create_period(create_bell_schedule(foreign_campus), "P1", "10:00", "10:45")

        response = self.schedule(self.a_physics, foreign_period)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(TimetableEntry.objects.exists())


class AcademicsGuardTests(TimetableTestCase):
    """What academics refuses once lessons are on the timetable."""

    def setUp(self):
        super().setUp()
        self.term = create_term(self.year)
        self.entry = create_timetable_entry(self.a_physics, self.p1, room=self.r101, term=self.term)
        self.authenticate(user_with_system_role(self.org, "org-admin", email="p@kmc.test"))

    def test_assignment_room_and_term_in_use_cannot_be_deleted(self):
        for url in (f"teaching-assignments/{self.a_physics.pk}", f"rooms/{self.r101.pk}",
                    f"terms/{self.term.pk}"):
            self.assertEqual(self.client.delete(f"{API}/{url}/").status_code, 409, url)

    def test_assignment_can_go_once_its_lessons_are_removed(self):
        self.client.delete(f"{API}/timetable/{self.entry.pk}/")

        self.assertEqual(self.client.delete(f"{API}/teaching-assignments/{self.a_physics.pk}/").status_code, 204)

    def test_teacher_of_a_timetabled_assignment_cannot_be_swapped(self):
        swapped = self.client.patch(f"{API}/teaching-assignments/{self.a_physics.pk}/", {"teacher": self.sita.pk})
        unscheduled = self.client.patch(f"{API}/teaching-assignments/{self.b_physics.pk}/", {"teacher": self.sita.pk})

        self.assertEqual(swapped.status_code, 400)
        self.assertEqual(unscheduled.status_code, 200)

    def test_timetabled_section_cannot_change_campus(self):
        response = self.client.patch(f"{API}/sections/{self.section_a.pk}/", {"campus": self.bhaktapur.pk})
        untimetabled = self.client.patch(f"{API}/sections/{self.section_b.pk}/", {"home_room": None}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("timetable", str(response.data))
        self.assertEqual(untimetabled.status_code, 200)
