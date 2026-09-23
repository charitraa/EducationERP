"""Placing students in sections, and the history that keeps."""
from datetime import date

from tests.base import APITestCaseBase
from tests.factories import (
    create_academic_year,
    create_campus,
    create_organization,
    create_program,
    create_section,
    create_student,
    user_with_permissions,
    user_with_system_role,
)

from ..models import Enrollment

URL = "/api/v1/students/"


class PlacementTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.program = create_program(self.org, first_level=1, last_level=10, code="school", name="School")
        self.year = create_academic_year(self.org, start="2025-04-14", end="2026-04-13")
        self.next_year = create_academic_year(self.org, name="2083/84", start="2026-04-14", end="2027-04-13")
        self.grade5a = create_section(self.campus, self.program, self.year, level=5, name="A")
        self.grade5b = create_section(self.campus, self.program, self.year, level=5, name="B")
        self.grade6a = create_section(self.campus, self.program, self.next_year, level=6, name="A")
        self.student = create_student(self.campus, admitted_on=date(2025, 4, 14))
        self.authenticate(user_with_permissions(self.org, ["students.view", "students.place"], email="o@kmc.test"))

    def place(self, section, **data):
        return self.client.post(f"{URL}{self.student.pk}/place/", {"section": section.pk, **data})

    def history(self):
        return list(
            Enrollment.objects.filter(student=self.student)
            .order_by("started_on", "pk")
            .values_list("section__name", "section__level", "status")
        )

    def test_first_placement_fills_the_open_enrollment(self):
        response = self.place(self.grade5a, on_date="2025-05-01")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["current_enrollment"]["section_name"], "Grade 5 A")
        self.assertEqual(response.data["current_enrollment"]["academic_year_name"], "2082/83")
        self.assertEqual(self.history(), [("A", 5, "active")])

    def test_section_change_keeps_history(self):
        self.place(self.grade5a, on_date="2025-05-01")

        self.place(self.grade5b, on_date="2025-06-01", reason="Balancing class sizes")

        self.assertEqual(self.history(), [("A", 5, "moved"), ("B", 5, "active")])

    def test_promotion_to_the_next_year(self):
        self.place(self.grade5a, on_date="2025-05-01")

        response = self.place(self.grade6a, on_date="2026-04-14", reason="Promoted")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.history(), [("A", 5, "moved"), ("A", 6, "active")])
        self.client.force_authenticate(None)
        self.authenticate(user_with_system_role(self.org, "org-admin", email="p@kmc.test"))
        listed = self.client.get(f"{URL}{self.student.pk}/enrollments/").data
        self.assertEqual([e["section_name"] for e in listed], ["Grade 6 A", "Grade 5 A"])

    def test_a_year_that_has_ended_is_refused(self):
        response = self.place(self.grade5a, on_date="2026-05-01")  # 2082/83 ended 2026-04-13

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "year_ended")

    def test_placing_ahead_into_next_year_is_allowed(self):
        self.place(self.grade5a, on_date="2025-05-01")

        response = self.place(self.grade6a, on_date="2026-03-20", reason="Promoted early")

        self.assertEqual(response.status_code, 200, response.data)

    def test_same_section_again_is_refused(self):
        self.place(self.grade5a, on_date="2025-05-01")

        self.assertEqual(self.place(self.grade5a, on_date="2025-05-01").status_code, 400)

    def test_section_at_another_campus_needs_a_transfer_first(self):
        other = create_section(create_campus(self.org, code="other"), self.program, self.year, level=5)

        response = self.place(other, on_date="2025-05-01")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Transfer", response.data["error"]["message"])

    def test_graduated_student_cannot_be_placed(self):
        from ..services import change_student_status

        change_student_status(student=self.student, status="graduated")

        self.assertEqual(self.place(self.grade5a, on_date="2025-05-01").status_code, 409)

    def test_another_organizations_section_is_unknown(self):
        foreign_org = create_organization(code="other")
        foreign = create_section(
            create_campus(foreign_org, code="main"), create_program(foreign_org), create_academic_year(foreign_org)
        )

        self.assertEqual(self.place(foreign, on_date="2025-05-01").status_code, 400)

    def test_placing_needs_the_place_permission(self):
        self.authenticate(user_with_permissions(self.org, ["students.view", "students.update"], email="u@kmc.test"))

        self.assertEqual(self.place(self.grade5a, on_date="2025-05-01").status_code, 403)

    def test_transfer_leaves_the_new_enrollment_unplaced(self):
        from ..services import transfer_student

        self.place(self.grade5a, on_date="2025-05-01")
        transfer_student(student=self.student, to_campus=create_campus(self.org, code="other"),
                         on_date=date(2025, 6, 1))

        current = Enrollment.objects.get(student=self.student, status="active")
        self.assertIsNone(current.section)
