"""Students' elective choices and moving a whole class at once."""
from datetime import timedelta

from tests.base import APITestCaseBase
from tests.factories import (
    add_to_curriculum,
    create_academic_year,
    create_campus,
    create_organization,
    create_program,
    create_section,
    create_staff_member,
    create_student,
    create_subject,
    create_teaching_assignment,
    user_with_system_role,
)

from modules.students.models import Enrollment
from modules.students.services import place_student

from ..models import StudentElective

API = "/api/v1"


class ElectiveTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.lalitpur = create_campus(self.org, code="lalitpur", name="Lalitpur")
        self.bhaktapur = create_campus(self.org, code="bhaktapur", name="Bhaktapur")
        self.year = create_academic_year(self.org)
        self.program = create_program(self.org)  # Grade 11–12
        self.physics = create_subject(self.org, code="physics", name="Physics")
        self.computer = create_subject(self.org, code="computer", name="Computer Science")
        self.biology = create_subject(self.org, code="biology", name="Biology")
        add_to_curriculum(self.program, self.physics, level=11)
        add_to_curriculum(self.program, self.computer, level=11, is_elective=True)
        add_to_curriculum(self.program, self.biology, level=11, is_elective=True)
        self.a = create_section(self.lalitpur, self.program, self.year, name="A")
        self.b = create_section(self.lalitpur, self.program, self.year, name="B")
        self.ram = create_student(self.lalitpur, student_number="S-1", first_name="Ram")
        self.sita = create_student(self.lalitpur, student_number="S-2", first_name="Sita")
        place_student(student=self.ram, section=self.a)
        place_student(student=self.sita, section=self.a)
        self.authenticate(user_with_system_role(self.org, "campus-admin", email="h@kmc.test", campus=self.lalitpur))

    def choose(self, student, subject):
        return self.client.post(f"{API}/student-electives/", {"student": student.pk, "subject": subject.pk})


class ElectiveTests(ElectiveTestCase):
    def test_record_a_choice(self):
        response = self.choose(self.ram, self.computer)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["section_name"], "Grade 11 A")
        self.assertEqual(response.data["student_number"], "S-1")

    def test_only_electives_of_the_students_level(self):
        compulsory = self.choose(self.ram, self.physics)
        elsewhere = create_subject(self.org, code="art", name="Art")

        self.assertEqual(compulsory.status_code, 400)
        self.assertEqual(self.choose(self.ram, elsewhere).status_code, 400)

    def test_no_duplicates_and_an_unplaced_student_has_no_class(self):
        self.choose(self.ram, self.computer)
        unplaced = create_student(self.lalitpur, student_number="S-3")

        self.assertEqual(self.choose(self.ram, self.computer).status_code, 400)
        self.assertIn("student", self.choose(unplaced, self.computer).data["error"]["details"])

    def test_two_electives_taught_at_the_same_time_cant_both_be_taken(self):
        from tests.factories import create_bell_schedule, create_period, create_timetable_entry

        period = create_period(create_bell_schedule(self.lalitpur), "P1", "10:00", "10:45")
        for subject, number in ((self.computer, "E-1"), (self.biology, "E-2")):
            assignment = create_teaching_assignment(self.a, subject, create_staff_member(self.lalitpur, employee_number=number))
            create_timetable_entry(assignment, period)
        self.choose(self.ram, self.computer)

        response = self.choose(self.ram, self.biology)

        self.assertEqual(response.status_code, 400)
        self.assertIn("same time", str(response.data))

    def test_students_of_a_section_by_subject(self):
        self.choose(self.ram, self.computer)

        compulsory = self.client.get(f"{API}/sections/{self.a.pk}/students/", {"subject": self.physics.pk})
        elective = self.client.get(f"{API}/sections/{self.a.pk}/students/", {"subject": self.computer.pk})

        self.assertEqual([s["student_number"] for s in compulsory.data], ["S-1", "S-2"])
        self.assertEqual([s["student_number"] for s in elective.data], ["S-1"])

    def test_a_move_within_the_level_keeps_choices_a_promotion_does_not(self):
        self.choose(self.ram, self.computer)
        next_year = create_academic_year(self.org, name="2083/84", start=self.year.end_date + timedelta(days=1))
        grade_12 = create_section(self.lalitpur, self.program, next_year, level=12, name="A")

        place_student(student=self.ram, section=self.b)
        in_b = Enrollment.objects.get(student=self.ram, status="active")
        self.assertEqual(list(in_b.electives.values_list("subject__code", flat=True)), ["computer"])

        place_student(student=self.ram, section=grade_12)
        in_12 = Enrollment.objects.get(student=self.ram, status="active")
        self.assertFalse(in_12.electives.exists())
        # History keeps each class's choices.
        self.assertEqual(StudentElective.objects.filter(enrollment__student=self.ram).count(), 2)

    def test_earlier_choices_are_history(self):
        choice = self.choose(self.ram, self.computer).data["id"]
        place_student(student=self.ram, section=self.b)
        old = StudentElective.objects.get(pk=choice)

        self.assertEqual(self.client.delete(f"{API}/student-electives/{old.pk}/").status_code, 409)
        current = self.client.get(f"{API}/student-electives/", {"student": self.ram.pk, "current": "true"})
        self.assertEqual([c["section_name"] for c in current.data["results"]], ["Grade 11 B"])

    def test_campus_scope(self):
        far = create_student(self.bhaktapur, student_number="S-9")
        far_section = create_section(self.bhaktapur, self.program, self.year, name="A")
        place_student(student=far, section=far_section)

        self.assertEqual(self.choose(far, self.computer).status_code, 403)


class PromotionTests(ElectiveTestCase):
    def setUp(self):
        super().setUp()
        self.next_year = create_academic_year(
            self.org, name="2083/84", start=self.year.end_date + timedelta(days=1)
        )
        self.grade_12 = create_section(self.lalitpur, self.program, self.next_year, level=12, name="A")

    def promote(self, section, **data):
        return self.client.post(f"{API}/sections/{section.pk}/promote/", data, format="json")

    def test_promote_a_whole_class(self):
        response = self.promote(self.a, to_section=self.grade_12.pk, reason="Passed Grade 11")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(sorted(s["student_number"] for s in response.data), ["S-1", "S-2"])
        self.assertEqual(self.grade_12.enrollments.filter(status="active").count(), 2)
        self.assertEqual(
            Enrollment.objects.get(student=self.ram, section=self.a).end_reason, "Passed Grade 11"
        )

    def test_students_held_back_stay(self):
        self.promote(self.a, to_section=self.grade_12.pk, exclude=[self.sita.pk])

        self.assertEqual(
            Enrollment.objects.get(student=self.sita, status="active").section_id, self.a.pk
        )

    def test_all_or_nothing(self):
        # A section of a year that has already ended refuses every student.
        old_year = create_academic_year(self.org, name="2080/81", start="2020-01-01", end="2020-12-31")
        ended = create_section(self.lalitpur, self.program, old_year, level=12, name="Z")

        response = self.promote(self.a, to_section=ended.pk)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "promotion_failed")
        self.assertEqual(len(response.data["error"]["details"]["students"]), 2)
        self.assertEqual(self.a.enrollments.filter(status="active").count(), 2)

    def test_target_must_be_another_section_at_the_campus(self):
        far = create_section(self.bhaktapur, self.program, self.next_year, level=12, name="A")

        self.assertEqual(self.promote(self.a, to_section=self.a.pk).status_code, 400)
        self.assertEqual(self.promote(self.a, to_section=far.pk).status_code, 400)

    def test_empty_class(self):
        empty = create_section(self.lalitpur, self.program, self.year, name="C")

        self.assertEqual(self.promote(empty, to_section=self.grade_12.pk).status_code, 400)

    def test_needs_the_place_permission(self):
        self.authenticate(user_with_system_role(self.org, "staff", email="t@kmc.test"))

        self.assertEqual(self.promote(self.a, to_section=self.grade_12.pk).status_code, 403)

    def test_one_failure_rolls_back_the_others(self):
        from unittest import mock

        from core.common.exceptions import ServiceError
        from modules.students import services

        real = services.place_student

        def fail_for_sita(*, student, **kwargs):
            if student.pk == self.sita.pk:
                raise ServiceError("Nope.", code="test_failure")
            return real(student=student, **kwargs)

        with mock.patch.object(services, "place_student", side_effect=fail_for_sita):
            response = self.promote(self.a, to_section=self.grade_12.pk)

        self.assertEqual(response.status_code, 409)
        self.assertEqual([f["student_number"] for f in response.data["error"]["details"]["students"]], ["S-2"])
        # Ram was moved before Sita failed; that move was undone.
        self.assertEqual(Enrollment.objects.get(student=self.ram, status="active").section_id, self.a.pk)


class StudentsTakingTests(ElectiveTestCase):
    def test_an_elective_from_an_earlier_class_doesnt_count(self):
        """Ram chose Computer in 11 A, moved to 11 B (keeping it), then
        dropped it there: he no longer takes Computer in 11 B."""
        from ..selectors import students_taking

        self.choose(self.ram, self.computer)
        place_student(student=self.ram, section=self.b)
        StudentElective.objects.filter(enrollment__student=self.ram, enrollment__status="active").delete()

        self.assertEqual(list(students_taking(self.b, self.computer.pk)), [])
