"""A +2 Science campus that sits exams.

Grade 11 has two sections (A and B). Physics and Mathematics are compulsory;
Computer Science and Biology are electives. Everything happens in the past
part of the academic year, since marks can't be entered for a paper that
hasn't been sat.
"""
from datetime import date, time, timedelta
from decimal import Decimal

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
    create_student_elective,
    create_subject,
    create_teaching_assignment,
    user_with_system_role,
)

from modules.examinations import services
from modules.examinations.models import Exam, ExamComponent, ExamSubject, ExamType, GradeScale
from modules.students.models import Enrollment
from modules.students.services import place_student

TODAY = date.today()
API = "/api/v1"


class ExamTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="lalitpur", name="Lalitpur")
        self.other_campus = create_campus(self.org, code="bhaktapur", name="Bhaktapur")
        self.year = create_academic_year(self.org, start=TODAY - timedelta(days=120), end=TODAY + timedelta(days=240))
        self.admitted = self.year.start_date

        self.program = create_program(self.org, first_level=11, last_level=12, attendance_mode="lesson")
        self.physics = create_subject(self.org, code="physics", name="Physics", credit_hours=Decimal("4"))
        self.math = create_subject(self.org, code="math", name="Mathematics", credit_hours=Decimal("4"))
        self.computer = create_subject(self.org, code="computer", name="Computer Science", credit_hours=Decimal("3"))
        self.biology = create_subject(self.org, code="biology", name="Biology", credit_hours=Decimal("3"))
        add_to_curriculum(self.program, self.physics, level=11)
        add_to_curriculum(self.program, self.math, level=11)
        add_to_curriculum(self.program, self.computer, level=11, is_elective=True)
        add_to_curriculum(self.program, self.biology, level=11, is_elective=True)
        self.section_a = create_section(self.campus, self.program, self.year, level=11, name="A")
        self.section_b = create_section(self.campus, self.program, self.year, level=11, name="B")

        self.hari = self.staff("E-1", "Hari")
        self.sita = self.staff("E-2", "Sita")
        for section in (self.section_a, self.section_b):
            create_teaching_assignment(section, self.physics, self.hari)
            create_teaching_assignment(section, self.math, self.sita)
        create_teaching_assignment(self.section_a, self.computer, self.sita)

        # Section A: Ram (computer), Shyam (biology). Section B: Gita, Binu (both computer).
        self.ram = self.student("S-1", "Ram", self.section_a, self.computer)
        self.shyam = self.student("S-2", "Shyam", self.section_a, self.biology)
        self.gita = self.student("S-3", "Gita", self.section_b, self.computer)
        self.binu = self.student("S-4", "Binu", self.section_b, self.computer)

        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test", campus=self.campus)
        self.principal = user_with_system_role(self.org, "org-admin", email="principal@kmc.test")

        self.scale = self.make_scale()
        self.terminal = ExamType.objects.create(organization=self.org, code="terminal", name="Terminal")

    # -- people --------------------------------------------------------------
    def staff(self, number, name):
        staff = create_staff_member(self.campus, employee_number=number, first_name=name)
        user = user_with_system_role(self.org, "staff", email=f"{name.lower()}@kmc.test")
        staff.user = user
        staff.save(update_fields=["user"])
        return staff

    def student(self, number, name, section, elective=None, campus=None):
        student = create_student(campus or self.campus, student_number=number, first_name=name,
                                 admitted_on=self.admitted)
        place_student(student=student, section=section)
        if elective is not None:
            create_student_elective(Enrollment.objects.on(TODAY).get(student=student), elective)
        return student

    def enrollment(self, student, day=None):
        return Enrollment.objects.on(day or TODAY).get(student=student)

    def login(self, who):
        self.logout()
        self.authenticate(getattr(who, "user", None) or who)

    # -- grading -------------------------------------------------------------
    def make_scale(self, program=None, preset="neb-style", **fields):
        scale = GradeScale.objects.create(organization=self.org, program=program, name="Scale", **fields)
        bands, divisions = services.preset_bands(preset)
        services.replace_bands(scale, bands, divisions)
        return scale

    # -- exams ---------------------------------------------------------------
    def make_exam(self, name="First Terminal", papers=(("physics", 3), ("math", 2)), status="scheduled",
                  full=100, pass_marks=35, **fields):
        """An exam with one theory component per paper on the given days-ago.
        ``papers`` is ((subject attribute, days ago), ...)."""
        fields.setdefault("grade_scale", self.scale)
        exam = Exam.objects.create(
            organization=self.org, campus=self.campus, academic_year=self.year, exam_type=self.terminal,
            program=self.program, name=name, **fields)
        for attr, days_ago in papers:
            self.paper(exam, getattr(self, attr), days_ago, full=full, pass_marks=pass_marks)
        if status in ("scheduled", "published"):
            services.schedule(exam)
        return exam

    def paper(self, exam, subject, days_ago, level=11, full=100, pass_marks=35, start="10:00", end="12:00"):
        paper = ExamSubject.objects.create(
            organization=self.org, exam=exam, subject=subject, level=level,
            date=TODAY - timedelta(days=days_ago), start_time=time.fromisoformat(start),
            end_time=time.fromisoformat(end))
        ExamComponent.objects.create(organization=self.org, exam_subject=paper, kind="theory", name="Theory",
                                     full_marks=Decimal(full), pass_marks=Decimal(pass_marks))
        return paper

    def paper_of(self, exam, subject, level=11):
        return exam.subjects.get(subject=subject, level=level)

    def sheet(self, exam, subject, section, by=None):
        """The mark sheet for a paper and class, opened as its teacher."""
        paper = self.paper_of(exam, subject)
        sheet, _ = services.open_sheet(paper, section, by=by or self.office)
        return sheet

    def fill(self, sheet, marks, by=None):
        """Enter marks for a sheet: ``{student: marks}``, or a status string
        such as "absent". Uses the first component."""
        paper = sheet.exam_subject
        component = paper.components.first()
        entries = []
        for student, value in marks.items():
            enrollment = self.enrollment(student, paper.date)
            if isinstance(value, str):
                entries.append(services.MarkEntry(enrollment.pk, component.pk, value, None))
            else:
                entries.append(services.MarkEntry(enrollment.pk, component.pk, "present", Decimal(str(value))))
        return services.enter_marks(sheet=sheet, entries=entries, by=by or self.office)

    def mark_all(self, exam, subject, marks_by_section, verify=True):
        """Open, fill, submit and verify the sheets of one paper."""
        for section, marks in marks_by_section.items():
            sheet = self.sheet(exam, subject, section)
            self.fill(sheet, marks)
            services.submit_sheet(sheet, by=self.office)
            if verify:
                services.verify_sheet(sheet, by=self.office)

    def finish_marking(self, exam, physics=None, math=None):
        """Marks for everyone in both papers, submitted and verified."""
        physics = physics or {self.ram: 80, self.shyam: 60, self.gita: 90, self.binu: 30}
        math = math or {self.ram: 70, self.shyam: 50, self.gita: 85, self.binu: 40}
        split = lambda marks: {
            self.section_a: {s: m for s, m in marks.items() if s in (self.ram, self.shyam)},
            self.section_b: {s: m for s, m in marks.items() if s in (self.gita, self.binu)},
        }
        self.mark_all(exam, self.physics, split(physics))
        self.mark_all(exam, self.math, split(math))

    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.data)
        self.assertEqual(response.data["error"]["code"], code, response.data)
