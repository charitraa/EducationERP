"""/timetable/me/ for teachers, students and parents."""
from tests.factories import (
    create_parent,
    create_student,
    create_teaching_assignment,
    create_timetable_entry,
    create_user,
)

from modules.academics.models import StudentElective
from modules.parents.services import link_student
from modules.students.models import Enrollment
from modules.students.services import place_student

from .test_entries import API, MONDAY, TUESDAY, TimetableTestCase
from .test_lesson_changes import next_monday

ME = f"{API}/timetable/me/"


class MyTimetableTests(TimetableTestCase):
    def setUp(self):
        super().setUp()
        self.computer_ta = create_teaching_assignment(self.section_a, self.computer, self.sita)
        self.biology_ta = create_teaching_assignment(self.section_a, self.biology, self.hari)
        create_timetable_entry(self.a_physics, self.p1, MONDAY, room=self.r101)
        create_timetable_entry(self.computer_ta, self.p2, MONDAY, room=self.r101)
        create_timetable_entry(self.biology_ta, self.p2, MONDAY, room=self.r102)
        create_timetable_entry(self.b_physics, self.p1, TUESDAY, room=self.r102)

        self.ram = create_student(self.lalitpur, student_number="S-1", first_name="Ram")
        place_student(student=self.ram, section=self.section_a)

    def login_as(self, email, **profile):
        user = create_user(self.org, email=email)
        for record in profile.values():
            record.user = user
            record.save(update_fields=["user"])
        self.authenticate(user)
        return user

    def subjects(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        return [lesson["subject_name"] for lesson in response.data["lessons"]]

    def test_a_teachers_week(self):
        self.login_as("hari@kmc.test", staff=self.hari)

        response = self.client.get(ME)

        self.assertEqual(response.data["as"], "teacher")
        self.assertEqual(self.subjects(response), ["Physics", "Biology", "Physics"])

    def test_a_teachers_day_includes_cover(self):
        from ..models import LessonChange, TimetableEntry

        entry = TimetableEntry.objects.get(teaching_assignment=self.computer_ta)
        monday = next_monday()
        LessonChange.objects.create(organization=self.org, entry=entry, date=monday,
                                    substitute_teacher=self.hari, room=None)
        self.login_as("hari@kmc.test", staff=self.hari)

        response = self.client.get(ME, {"date": monday.isoformat()})

        self.assertEqual(sorted(self.subjects(response)), ["Biology", "Computer Science", "Physics"])

    def test_a_student_sees_compulsory_subjects_and_their_electives(self):
        self.login_as("ram.student@kmc.test", student=self.ram)
        everything = self.subjects(self.client.get(ME))
        enrollment = Enrollment.objects.get(student=self.ram, status="active")
        StudentElective.objects.create(
            organization=self.org, subject=self.computer, enrollment=enrollment,
            started_on=enrollment.started_on,
        )

        chosen = self.subjects(self.client.get(ME))

        # Before any choice is recorded, every elective shows.
        self.assertEqual(everything, ["Physics", "Computer Science", "Biology"])
        self.assertEqual(chosen, ["Physics", "Computer Science"])

    def test_an_unplaced_student_has_no_lessons(self):
        newcomer = create_student(self.lalitpur, student_number="S-2")
        self.login_as("new@kmc.test", student=newcomer)

        self.assertEqual(self.subjects(self.client.get(ME)), [])

    def test_a_parent_sees_their_childs_timetable(self):
        parent = create_parent(self.org)
        link_student(parent=parent, student=self.ram, relationship="father")
        self.login_as("dad@kmc.test", parent=parent)

        response = self.client.get(ME)

        self.assertEqual((response.data["as"], response.data["student"]), ("parent", self.ram.pk))
        self.assertIn("Physics", self.subjects(response))

    def test_a_parent_of_several_children_chooses_one(self):
        parent = create_parent(self.org)
        sita = create_student(self.lalitpur, student_number="S-2")
        place_student(student=sita, section=self.section_b)
        for child in (self.ram, sita):
            link_student(parent=parent, student=child, relationship="mother")
        stranger = create_student(self.lalitpur, student_number="S-3")
        self.login_as("mum@kmc.test", parent=parent)

        self.assertEqual(self.client.get(ME).status_code, 400)
        self.assertEqual(self.subjects(self.client.get(ME, {"student": sita.pk})), ["Physics"])
        self.assertEqual(self.client.get(ME, {"student": stranger.pk}).status_code, 404)

    def test_no_profile(self):
        self.login_as("nobody@kmc.test")

        self.assertEqual(self.client.get(ME).status_code, 404)
