"""A +2 Science campus that bills fees.

Grade 11 has two sections (A and B). One fee structure covers tuition and
admission for the program at that level, for this academic year. Ram and
Shyam are in section A; Gita and Binu in section B.
"""
from datetime import date, timedelta
from decimal import Decimal

from tests.base import APITestCaseBase
from tests.factories import (
    create_academic_year,
    create_campus,
    create_organization,
    create_program,
    create_section,
    create_student,
    create_term,
    user_with_system_role,
)

from modules.finance.models import FeeCategory, FeeStructure, FeeStructureItem, Frequency, Invoice
from modules.students.models import Enrollment
from modules.students.services import place_student

TODAY = date.today()
API = "/api/v1"


class FinanceTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="lalitpur", name="Lalitpur")
        self.other_campus = create_campus(self.org, code="bhaktapur", name="Bhaktapur")
        self.year = create_academic_year(self.org, start=TODAY - timedelta(days=60), end=TODAY + timedelta(days=300))
        self.term1 = create_term(self.year, sequence=1, name="Term 1", start=self.year.start_date,
                                 end=TODAY + timedelta(days=60))

        self.program = create_program(self.org, first_level=11, last_level=12)
        self.section_a = create_section(self.campus, self.program, self.year, level=11, name="A")
        self.section_b = create_section(self.campus, self.program, self.year, level=11, name="B")

        self.ram = self.student("S-1", "Ram", self.section_a)
        self.shyam = self.student("S-2", "Shyam", self.section_a)
        self.gita = self.student("S-3", "Gita", self.section_b)
        self.binu = self.student("S-4", "Binu", self.section_b)

        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test", campus=self.campus)
        self.principal = user_with_system_role(self.org, "org-admin", email="principal@kmc.test")

        self.tuition = FeeCategory.objects.create(organization=self.org, code="tuition", name="Tuition")
        self.admission_fee = FeeCategory.objects.create(organization=self.org, code="admission", name="Admission")
        self.structure = FeeStructure.objects.create(organization=self.org, program=self.program, level=11,
                                                     academic_year=self.year, name="+2 Science Grade 11")
        self.tuition_item = FeeStructureItem.objects.create(
            organization=self.org, fee_structure=self.structure, category=self.tuition, amount=Decimal("10000"),
            frequency=Frequency.PER_TERM)
        self.admission_item = FeeStructureItem.objects.create(
            organization=self.org, fee_structure=self.structure, category=self.admission_fee, amount=Decimal("2000"),
            frequency=Frequency.ONE_TIME)

    # -- people ---------------------------------------------------------------
    def student(self, number, name, section):
        student = create_student(section.campus, student_number=number, first_name=name,
                                 admitted_on=self.year.start_date)
        place_student(student=student, section=section)
        return student

    def enrollment(self, student, day=None):
        return Enrollment.objects.on(day or TODAY).get(student=student)

    def login(self, who):
        self.logout()
        self.authenticate(getattr(who, "user", None) or who)

    # -- invoices ---------------------------------------------------------------
    def bill_term(self, term=None, structure=None, **fields):
        from modules.finance import services

        return services.generate_term_invoices(structure or self.structure, term or self.term1, by=self.office,
                                               **fields)

    def invoice_of(self, student, term=None):
        return Invoice.objects.get(student=student, term=term or self.term1)

    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.data)
        self.assertEqual(response.data["error"]["code"], code, response.data)
