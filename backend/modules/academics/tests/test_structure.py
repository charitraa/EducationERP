"""Organization-wide structure: departments, programs, subjects, curriculum,
academic years and terms."""
from django.db import IntegrityError, transaction

from tests.base import APITestCaseBase
from tests.factories import (
    add_to_curriculum,
    create_academic_year,
    create_campus,
    create_organization,
    create_program,
    create_section,
    create_staff_member,
    create_subject,
    user_with_permissions,
    user_with_system_role,
)

from ..models import AcademicYear, Program

API = "/api/v1"
MANAGE = ["academics.view", "academics.manage_structure"]


class StructureTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.admin = user_with_permissions(self.org, MANAGE, email="academic@kmc.test")
        self.authenticate(self.admin)


class DepartmentProgramSubjectTests(StructureTestCase):
    def test_build_a_department_program_and_subject(self):
        head = create_staff_member(self.campus)
        dept = self.client.post(f"{API}/departments/", {"code": "Science", "name": "Science", "head": head.pk})
        program = self.client.post(f"{API}/programs/", {
            "code": "bsc-csit", "name": "BSc CSIT", "department": dept.data["id"],
            "level_type": "semester", "first_level": 1, "last_level": 8,
        })
        subject = self.client.post(f"{API}/subjects/", {
            "code": "csc101", "name": "Introduction to IT", "department": dept.data["id"], "credit_hours": "3",
        })

        self.assertEqual(dept.status_code, 201, dept.data)
        self.assertEqual(dept.data["code"], "science")  # stored lowercase
        self.assertEqual(dept.data["head_name"], head.full_name)
        self.assertEqual(program.status_code, 201, program.data)
        self.assertEqual(subject.status_code, 201, subject.data)

    def test_codes_are_unique_per_organization(self):
        create_program(self.org, code="plus2-science")

        same = self.client.post(f"{API}/programs/", {"code": "plus2-science", "name": "X"})
        elsewhere = create_program(create_organization(code="other"), code="plus2-science")

        self.assertEqual(same.status_code, 400)
        self.assertIn("code", same.data["error"]["details"])
        self.assertIsNotNone(elsewhere.pk)

    def test_levels_must_be_ordered(self):
        response = self.client.post(f"{API}/programs/", {
            "code": "bad", "name": "Bad", "first_level": 5, "last_level": 2,
        })

        self.assertEqual(response.status_code, 400)

    def test_database_also_orders_levels(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Program.objects.create(organization=self.org, code="x", name="X", first_level=5, last_level=2)

    def test_range_cannot_shrink_past_existing_sections(self):
        program = create_program(self.org, first_level=11, last_level=12)
        create_section(self.campus, program, create_academic_year(self.org), level=12)

        response = self.client.patch(f"{API}/programs/{program.pk}/", {"last_level": 11})

        self.assertEqual(response.status_code, 400)

    def test_head_from_another_organization_is_unknown(self):
        foreign = create_staff_member(create_campus(create_organization(code="other"), code="main"))

        response = self.client.post(f"{API}/departments/", {"code": "x", "name": "X", "head": foreign.pk})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(str(response.data["error"]["details"]["head"][0]), "Unknown staff member.")

    def test_a_program_in_use_cannot_be_deleted(self):
        program = create_program(self.org)
        add_to_curriculum(program, create_subject(self.org), level=11)

        response = self.client.delete(f"{API}/programs/{program.pk}/")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "in_use")

    def test_an_unused_subject_can_be_deleted(self):
        subject = create_subject(self.org)

        self.assertEqual(self.client.delete(f"{API}/subjects/{subject.pk}/").status_code, 204)


class CurriculumTests(StructureTestCase):
    def setUp(self):
        super().setUp()
        self.program = create_program(self.org, first_level=11, last_level=12)
        self.physics = create_subject(self.org)

    def add(self, **data):
        data = {"program": self.program.pk, "subject": self.physics.pk, "level": 11, **data}
        return self.client.post(f"{API}/curriculum/", data)

    def test_add_a_subject_to_a_level(self):
        response = self.add(is_elective=True)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["level_label"], "Grade 11")

    def test_level_outside_the_program_is_rejected(self):
        response = self.add(level=10)

        self.assertEqual(response.status_code, 400)
        self.assertIn("level", response.data["error"]["details"])

    def test_same_subject_twice_at_one_level_is_rejected(self):
        self.add()

        self.assertEqual(self.add().status_code, 400)

    def test_filter_one_levels_syllabus(self):
        maths = create_subject(self.org, code="maths", name="Mathematics")
        self.add()
        self.add(subject=maths.pk, level=12)

        response = self.client.get(f"{API}/curriculum/", {"program": self.program.pk, "level": 12})

        self.assertEqual([e["subject_code"] for e in response.data["results"]], ["maths"])


class AcademicYearAndTermTests(StructureTestCase):
    def year(self, name="2082/83", start="2025-04-14", end="2026-04-13"):
        return self.client.post(f"{API}/academic-years/", {"name": name, "start_date": start, "end_date": end})

    def test_create_with_a_bikram_sambat_name(self):
        response = self.year()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["name"], "2082/83")
        self.assertFalse(response.data["is_current"])

    def test_years_cannot_overlap(self):
        self.year()

        response = self.year(name="2083/84", start="2026-01-01", end="2026-12-31")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Overlaps", str(response.data["error"]))

    def test_end_must_follow_start(self):
        self.assertEqual(self.year(start="2026-01-01", end="2025-01-01").status_code, 400)

    def test_only_one_current_year(self):
        first = create_academic_year(self.org)
        second = create_academic_year(self.org, name="2083/84", start="2026-04-14", end="2027-04-13")

        self.client.post(f"{API}/academic-years/{first.pk}/set-current/")
        response = self.client.post(f"{API}/academic-years/{second.pk}/set-current/")

        self.assertEqual(response.status_code, 200)
        current = AcademicYear.objects.filter(organization=self.org, is_current=True)
        self.assertEqual([y.name for y in current], ["2083/84"])

    def test_database_refuses_two_current_years(self):
        create_academic_year(self.org, is_current=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_academic_year(self.org, name="x", start="2030-01-01", end="2030-12-31", is_current=True)

    def test_terms_must_fit_inside_the_year_and_not_overlap(self):
        year = create_academic_year(self.org, start="2025-04-14", end="2026-04-13")
        term = lambda **d: self.client.post(f"{API}/terms/", {"academic_year": year.pk, **d})  # noqa: E731

        first = term(name="First Term", sequence=1, start_date="2025-04-14", end_date="2025-08-15")
        overlapping = term(name="Second Term", sequence=2, start_date="2025-08-01", end_date="2025-12-15")
        outside = term(name="Third Term", sequence=3, start_date="2026-01-01", end_date="2026-05-30")
        same_number = term(name="Other", sequence=1, start_date="2025-09-01", end_date="2025-10-01")

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(overlapping.status_code, 400)
        self.assertEqual(outside.status_code, 400)
        self.assertEqual(same_number.status_code, 400)

    def test_a_year_with_sections_cannot_be_deleted(self):
        year = create_academic_year(self.org)
        create_section(self.campus, create_program(self.org), year)

        self.assertEqual(self.client.delete(f"{API}/academic-years/{year.pk}/").status_code, 409)


class StructurePermissionTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")

    def test_staff_can_read_the_structure_but_not_change_it(self):
        self.authenticate(user_with_system_role(self.org, "staff", email="t@kmc.test"))

        self.assertEqual(self.client.get(f"{API}/programs/").status_code, 200)
        self.assertEqual(self.client.post(f"{API}/programs/", {"code": "x", "name": "X"}).status_code, 403)

    def test_campus_admin_cannot_change_the_organization_structure(self):
        self.authenticate(user_with_system_role(self.org, "campus-admin", email="h@kmc.test"))

        self.assertEqual(self.client.post(f"{API}/subjects/", {"code": "x", "name": "X"}).status_code, 403)

    def test_other_organizations_structure_is_invisible(self):
        foreign = create_program(create_organization(code="other"))
        self.authenticate(user_with_permissions(self.org, MANAGE, email="a@kmc.test"))

        self.assertEqual(self.client.get(f"{API}/programs/").data["count"], 0)
        self.assertEqual(self.client.get(f"{API}/programs/{foreign.pk}/").status_code, 404)
