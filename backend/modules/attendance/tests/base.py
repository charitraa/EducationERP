"""A campus with both kinds of attendance.

+2 Science takes attendance in every lesson; the school (Grade 5) takes one
roll call a day with its class teacher. Everything happens on this week's
Monday, which is today or earlier, since attendance can't be taken ahead.
"""
from datetime import date, timedelta

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
    create_student,
    create_student_elective,
    create_subject,
    create_teaching_assignment,
    create_timetable_entry,
    user_with_system_role,
)

from modules.students.models import Enrollment
from modules.students.services import place_student
from modules.timetable.models import Weekday

API = "/api/v1/attendance"
TODAY = date.today()
MONDAY = TODAY - timedelta(days=TODAY.isoweekday() - 1)
SUNDAY = MONDAY - timedelta(days=1)


class AttendanceTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.lalitpur = create_campus(self.org, code="lalitpur", name="Lalitpur")
        self.bhaktapur = create_campus(self.org, code="bhaktapur", name="Bhaktapur")
        self.year = create_academic_year(self.org, start=TODAY - timedelta(days=120),
                                         end=TODAY + timedelta(days=240))
        self.admitted = self.year.start_date

        # +2: attendance in every lesson.
        self.plus2 = create_program(self.org, attendance_mode="lesson")
        self.physics = create_subject(self.org, code="physics", name="Physics")
        self.computer = create_subject(self.org, code="computer", name="Computer Science")
        self.biology = create_subject(self.org, code="biology", name="Biology")
        add_to_curriculum(self.plus2, self.physics, level=11)
        add_to_curriculum(self.plus2, self.computer, level=11, is_elective=True)
        add_to_curriculum(self.plus2, self.biology, level=11, is_elective=True)
        self.section_a = create_section(self.lalitpur, self.plus2, self.year, name="A")
        self.section_b = create_section(self.lalitpur, self.plus2, self.year, name="B")

        # School: one roll call a day, by the class teacher.
        self.school = create_program(self.org, code="school", name="School", first_level=1,
                                     last_level=10, attendance_mode="daily")
        self.gita = self.staff("E-3", "Gita")
        self.grade5 = create_section(self.lalitpur, self.school, self.year, level=5, name="A",
                                     class_teacher=self.gita)

        self.hari = self.staff("E-1", "Hari")
        self.sita = self.staff("E-2", "Sita")
        self.bell = create_bell_schedule(self.lalitpur)
        self.p1 = create_period(self.bell, "Period 1", "10:00", "10:45")
        self.p2 = create_period(self.bell, "Period 2", "10:45", "11:30")
        self.physics_a = create_timetable_entry(
            create_teaching_assignment(self.section_a, self.physics, self.hari), self.p1, Weekday.MONDAY)
        self.computer_a = create_timetable_entry(
            create_teaching_assignment(self.section_a, self.computer, self.sita), self.p2, Weekday.MONDAY)
        self.physics_b = create_timetable_entry(
            create_teaching_assignment(self.section_b, self.physics, self.sita), self.p2, Weekday.TUESDAY)

        # Ram takes Computer Science; Shyam takes Biology.
        self.ram = self.student("S-1", "Ram", self.section_a)
        self.shyam = self.student("S-2", "Shyam", self.section_a)
        create_student_elective(self.enrollment(self.ram), self.computer)
        create_student_elective(self.enrollment(self.shyam), self.biology)
        self.anu = self.student("S-3", "Anu", self.grade5)
        self.binu = self.student("S-4", "Binu", self.grade5)

        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test",
                                            campus=self.lalitpur)

    # -- helpers -------------------------------------------------------------
    def staff(self, number, name, campus=None):
        staff = create_staff_member(campus or self.lalitpur, employee_number=number, first_name=name)
        user = user_with_system_role(self.org, "staff", email=f"{name.lower()}@kmc.test")
        staff.user = user
        staff.save(update_fields=["user"])
        return staff

    def student(self, number, name, section):
        student = create_student(section.campus, student_number=number, first_name=name,
                                 admitted_on=self.admitted)
        place_student(student=student, section=section)
        return student

    def enrollment(self, student, day=None):
        return Enrollment.objects.on(day or TODAY).get(student=student)

    def login(self, who):
        """A staff member, or a user."""
        self.logout()
        self.authenticate(getattr(who, "user", None) or who)

    def open_lesson(self, entry, day=MONDAY):
        return self.client.post(f"{API}/sessions/", {"timetable_entry": entry.pk, "date": day.isoformat()})

    def open_daily(self, section, day=MONDAY):
        return self.client.post(f"{API}/sessions/", {"section": section.pk, "date": day.isoformat()})

    def mark(self, session_id, records=(), **extra):
        return self.client.post(f"{API}/sessions/{session_id}/mark/", {
            "records": [{"enrollment": e, "status": s} for e, s in records], **extra,
        })

    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.data)
        self.assertEqual(response.data["error"]["code"], code, response.data)
