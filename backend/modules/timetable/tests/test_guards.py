"""Real life: changes elsewhere that would quietly break the timetable."""
from datetime import timedelta

from django.utils import timezone

from tests.factories import (
    create_student,
    create_timetable_entry,
    user_with_system_role,
)

from modules.academics.models import CurriculumSubject, StudentElective
from modules.students.models import Enrollment
from modules.students.services import place_student

from ..models import LessonChange
from .test_entries import API, MONDAY, TimetableTestCase
from .test_lesson_changes import next_monday


class GuardTestCase(TimetableTestCase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.lesson = create_timetable_entry(self.a_physics, self.p1, MONDAY, room=self.r101)
        self.authenticate(user_with_system_role(self.org, "org-admin", email="p@kmc.test"))


class TeacherLeavesTests(GuardTestCase):
    def leave(self, member, on=None):
        return self.client.patch(f"{API}/staff/{member.pk}/", {
            "status": "left", "left_on": (on or self.today).isoformat()})

    def test_a_teacher_with_lessons_cant_just_leave(self):
        response = self.leave(self.hari)

        self.assertEqual(response.status_code, 400)
        self.assertIn("weekly lessons", str(response.data))

    def test_after_hand_over_they_can(self):
        self.client.post(f"{API}/timetable/hand-over/", {
            "teaching_assignments": [self.a_physics.pk, self.b_physics.pk], "teacher": self.sita.pk,
            "on": self.today.isoformat()}, format="json")

        self.assertEqual(self.leave(self.hari).status_code, 200)

    def test_planned_cover_and_class_teacher_duties_count_too(self):
        self.section_b.class_teacher = self.sita
        self.section_b.save()
        LessonChange.objects.create(organization=self.org, entry=self.lesson, date=next_monday(),
                                    substitute_teacher=self.sita)

        response = self.leave(self.sita)

        self.assertIn("cover", str(response.data))
        self.assertIn("class teacher of Grade 11 B", str(response.data))

    def test_lessons_that_end_before_the_leaving_date_dont_count(self):
        last_day = self.today + timedelta(days=30)
        self.lesson.valid_until = last_day
        self.lesson.save()

        self.assertEqual(self.leave(self.hari, on=last_day + timedelta(days=1)).status_code, 200)


class SubstituteOnLeaveTests(GuardTestCase):
    def test_a_teacher_on_leave_cant_cover(self):
        self.sita.status = "on_leave"
        self.sita.save()

        response = self.client.post(f"{API}/lesson-changes/", {
            "entry": self.lesson.pk, "date": next_monday().isoformat(), "substitute_teacher": self.sita.pk})

        self.assertEqual(response.status_code, 400)
        self.assertIn("on leave", str(response.data))


class RoomClosureTests(GuardTestCase):
    def test_a_room_with_lessons_cant_be_closed(self):
        closed = self.client.patch(f"{API}/rooms/{self.r101.pk}/", {"is_active": False})
        idle = self.client.patch(f"{API}/rooms/{self.r102.pk}/", {"is_active": False})

        self.assertEqual(closed.status_code, 400)
        self.assertEqual(idle.status_code, 200)

    def test_after_moving_the_lessons_it_can(self):
        self.client.patch(f"{API}/timetable/{self.lesson.pk}/", {"room": self.r102.pk})

        self.assertEqual(self.client.patch(f"{API}/rooms/{self.r101.pk}/", {"is_active": False}).status_code, 200)


class CurriculumTests(GuardTestCase):
    def setUp(self):
        super().setUp()
        self.ram = create_student(self.lalitpur, student_number="S-1")
        place_student(student=self.ram, section=self.section_a)

    def curriculum(self, subject):
        return CurriculumSubject.objects.get(subject=subject, level=11)

    def test_an_elective_students_took_cant_be_removed(self):
        enrollment = Enrollment.objects.get(student=self.ram)
        StudentElective.objects.create(organization=self.org, enrollment=enrollment, subject=self.computer,
                                       started_on=enrollment.started_on)

        response = self.client.delete(f"{API}/curriculum/{self.curriculum(self.computer).pk}/")

        self.assertEqual(response.status_code, 409)
        self.assertIn("take this elective", str(response.data))

    def test_an_elective_running_in_parallel_cant_become_compulsory(self):
        from tests.factories import create_teaching_assignment

        computer = create_teaching_assignment(self.section_a, self.computer, self.sita)
        biology = create_teaching_assignment(self.section_a, self.biology, self.hari)
        create_timetable_entry(computer, self.p2, MONDAY, room=self.r101)
        create_timetable_entry(biology, self.p2, MONDAY, room=self.r102)

        response = self.client.patch(f"{API}/curriculum/{self.curriculum(self.biology).pk}/", {"is_elective": False})

        self.assertEqual(response.status_code, 400)
        self.assertIn("same time", str(response.data))

    def test_an_elective_with_current_choices_cant_become_compulsory(self):
        enrollment = Enrollment.objects.get(student=self.ram)
        StudentElective.objects.create(organization=self.org, enrollment=enrollment, subject=self.computer,
                                       started_on=enrollment.started_on)

        response = self.client.patch(f"{API}/curriculum/{self.curriculum(self.computer).pk}/", {"is_elective": False})

        self.assertEqual(response.status_code, 400)

    def test_an_unused_elective_can_become_compulsory(self):
        response = self.client.patch(f"{API}/curriculum/{self.curriculum(self.computer).pk}/", {"is_elective": False})

        self.assertEqual(response.status_code, 200)
