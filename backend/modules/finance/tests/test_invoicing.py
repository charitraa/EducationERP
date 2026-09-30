"""Generating invoices from a fee structure, and standing scholarships."""
from datetime import timedelta
from decimal import Decimal as D

from modules.students.services import place_student
from tests.factories import create_student

from ..models import Invoice, InvoiceStatus, Scholarship, StudentScholarship
from .base import API, TODAY, FinanceTestCase

STRUCTURES = f"{API}/fee-structures/"
INVOICES = f"{API}/invoices/"


class TermInvoiceTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def generate(self, **body):
        return self.client.post(f"{STRUCTURES}{self.structure.pk}/generate-invoices/",
                                {"term": self.term1.pk, **body})

    def test_one_invoice_per_placed_student(self):
        r = self.generate()
        self.assertEqual((r.status_code, r.data), (200, {"created": 4, "skipped": 0}))
        invoice = self.invoice_of(self.ram)
        self.assertEqual((invoice.status, invoice.total, invoice.paid_amount, str(invoice.balance)),
                         (InvoiceStatus.ISSUED, D("10000.00"), D("0"), "10000.00"))
        self.assertTrue(invoice.invoice_number.startswith("INV-"))

    def test_running_it_again_only_bills_whoever_is_new(self):
        self.generate()
        late = self.student("S-9", "Late", self.section_a)
        r = self.generate()
        self.assertEqual(r.data, {"created": 1, "skipped": 4})
        self.assertTrue(Invoice.objects.filter(student=late, term=self.term1).exists())

    def test_a_student_who_joins_mid_term_is_billed_on_a_rerun(self):
        # Admitted and placed after the term began: not in any class on its
        # first day, so billing by that day alone would never reach them.
        self.generate()
        late = create_student(self.campus, student_number="S-9", first_name="Late", admitted_on=TODAY)
        place_student(student=late, section=self.section_a)
        r = self.generate()
        self.assertEqual(r.data, {"created": 1, "skipped": 4})
        self.assertTrue(Invoice.objects.filter(student=late, term=self.term1).exists())

    def test_only_per_term_items_are_billed(self):
        self.generate()
        invoice = self.invoice_of(self.ram)
        self.assertEqual(invoice.items.count(), 1)
        self.assertEqual(invoice.items.first().category, self.tuition)

    def test_a_term_outside_the_structures_year_is_refused(self):
        from tests.factories import create_academic_year, create_term

        other_year = create_academic_year(self.org, name="Other", start=TODAY - timedelta(days=900),
                                          end=TODAY - timedelta(days=500))
        other_term = create_term(other_year)
        r = self.client.post(f"{STRUCTURES}{self.structure.pk}/generate-invoices/", {"term": other_term.pk})
        self.assertError(r, 400, "wrong_year")

    def test_a_structure_with_no_per_term_items_is_refused(self):
        from ..models import FeeStructure, FeeStructureItem

        structure = FeeStructure.objects.create(organization=self.org, program=self.program, level=12,
                                                 academic_year=self.year, name="Grade 12")
        FeeStructureItem.objects.create(organization=self.org, fee_structure=structure, category=self.admission_fee,
                                        amount=D("500"), frequency="one_time")
        r = self.client.post(f"{STRUCTURES}{structure.pk}/generate-invoices/", {"term": self.term1.pk})
        self.assertError(r, 400, "no_items")

    def test_can_be_limited_to_one_section(self):
        r = self.generate(section=self.section_a.pk)
        self.assertEqual(r.data, {"created": 2, "skipped": 0})
        self.assertFalse(Invoice.objects.filter(student=self.gita).exists())

    def test_only_currently_placed_students_are_billed(self):
        moved = self.student("S-9", "Moved", self.section_a)
        place_student(student=moved, section=self.section_b, on_date=self.term1.start_date + timedelta(days=1))
        r = self.generate()
        self.assertEqual(r.data["created"], 5)
        self.assertEqual(self.invoice_of(moved).campus, self.campus)  # billed once, via whichever section matched

    def test_default_due_date_is_15_days_out(self):
        self.generate()
        invoice = self.invoice_of(self.ram)
        self.assertEqual(invoice.due_date, invoice.issue_date + timedelta(days=15))

    def test_a_teacher_cannot_generate_invoices(self):
        from tests.factories import user_with_system_role

        teacher = user_with_system_role(self.org, "staff", email="teacher@kmc.test")
        self.login(teacher)
        self.assertEqual(self.generate().status_code, 403)


class ScholarshipDiscountTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def grant(self, student, kind, value, category=None, started_on=None):
        scholarship = Scholarship.objects.create(organization=self.org, name=f"{kind}-{value}", kind=kind,
                                                  value=D(value), category=category)
        StudentScholarship.objects.create(organization=self.org, student=student, scholarship=scholarship,
                                          started_on=started_on or self.year.start_date)
        return scholarship

    def test_percentage_scholarship_reduces_the_invoice(self):
        self.grant(self.ram, "percentage", "50")
        self.bill_term()
        invoice = self.invoice_of(self.ram)
        self.assertEqual(invoice.total, D("5000.00"))
        line = invoice.items.get(kind="scholarship")
        self.assertEqual(line.amount, D("-5000.00"))

    def test_flat_scholarship(self):
        self.grant(self.shyam, "flat", "3000")
        self.bill_term()
        self.assertEqual(self.invoice_of(self.shyam).total, D("7000.00"))

    def test_a_flat_scholarship_cannot_push_the_total_negative(self):
        self.grant(self.ram, "flat", "50000")
        self.bill_term()
        self.assertEqual(self.invoice_of(self.ram).total, D("0.00"))

    def test_a_scholarship_narrowed_to_a_category_ignores_other_categories(self):
        # Bill an invoice that also has a one-time admission item, and check the tuition-only
        # scholarship doesn't touch it.
        self.grant(self.gita, "percentage", "100", category=self.tuition)
        self.bill_term()
        invoice = self.invoice_of(self.gita)
        self.assertEqual(invoice.total, D("0.00"))

    def test_two_scholarships_stack_without_exceeding_the_total(self):
        self.grant(self.binu, "percentage", "60")
        self.grant(self.binu, "flat", "8000")
        self.bill_term()
        # 60% off 10000 = 6000 taken first, leaving 4000; the flat 8000 is capped to that 4000.
        invoice = self.invoice_of(self.binu)
        self.assertEqual(invoice.total, D("0.00"))
        self.assertEqual(invoice.items.filter(kind="scholarship").count(), 2)

    def test_an_ended_scholarship_does_not_apply(self):
        scholarship = self.grant(self.ram, "flat", "1000")
        StudentScholarship.objects.filter(student=self.ram, scholarship=scholarship).update(
            ended_on=self.year.start_date + timedelta(days=1))
        self.bill_term()
        self.assertEqual(self.invoice_of(self.ram).total, D("10000.00"))

    def test_an_inactive_scholarship_does_not_apply(self):
        scholarship = self.grant(self.ram, "flat", "1000")
        scholarship.is_active = False
        scholarship.save(update_fields=["is_active"])
        self.bill_term()
        self.assertEqual(self.invoice_of(self.ram).total, D("10000.00"))


class OneTimeInvoiceTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def generate(self, student, **body):
        return self.client.post(f"{STRUCTURES}{self.structure.pk}/generate-one-time-invoice/",
                                {"student": student.pk, **body})

    def test_creates_a_one_time_invoice(self):
        r = self.generate(self.ram)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["total"], r.data["term"]), ("2000.00", None))

    def test_refused_a_second_time(self):
        self.generate(self.ram)
        self.assertError(self.generate(self.ram), 409, "already_invoiced")

    def test_a_structure_with_no_one_time_items_is_refused(self):
        from ..models import FeeStructure

        structure = FeeStructure.objects.create(organization=self.org, program=self.program, level=12,
                                                 academic_year=self.year, name="Grade 12")
        self.tuition_item.__class__.objects.create(organization=self.org, fee_structure=structure,
                                                    category=self.tuition, amount=D("1"), frequency="per_term")
        r = self.client.post(f"{STRUCTURES}{structure.pk}/generate-one-time-invoice/", {"student": self.ram.pk})
        self.assertError(r, 400, "no_items")

    def test_a_student_of_another_program_is_refused(self):
        from tests.factories import create_program, create_section

        other = create_program(self.org, code="CSIT", name="BSc CSIT", first_level=1, last_level=8)
        csit = create_section(self.campus, other, self.year, level=1, name="A")
        outsider = self.student("S-8", "Outsider", csit)
        self.assertError(self.generate(outsider), 400, "not_placed")
        self.assertFalse(Invoice.objects.filter(student=outsider).exists())

    def test_a_student_of_another_level_is_refused(self):
        from tests.factories import create_section

        grade12 = create_section(self.campus, self.program, self.year, level=12, name="A")
        senior = self.student("S-8", "Senior", grade12)
        self.assertError(self.generate(senior), 400, "not_placed")

    def test_an_unplaced_student_is_refused(self):
        unplaced = create_student(self.campus, student_number="S-8", first_name="New", admitted_on=TODAY)
        self.assertError(self.generate(unplaced), 400, "not_placed")

    def test_a_student_placed_ahead_into_the_class_can_be_billed(self):
        from tests.factories import create_academic_year, create_section

        next_year = create_academic_year(self.org, name="Next", start=self.year.end_date + timedelta(days=1),
                                         end=self.year.end_date + timedelta(days=365))
        structure = self.structure.__class__.objects.create(organization=self.org, program=self.program, level=11,
                                                            academic_year=next_year, name="Grade 11 next year")
        self.admission_item.__class__.objects.create(organization=self.org, fee_structure=structure,
                                                     category=self.admission_fee, amount=D("2500"),
                                                     frequency="one_time")
        next_a = create_section(self.campus, self.program, next_year, level=11, name="A")
        newcomer = create_student(self.campus, student_number="S-8", first_name="Next", admitted_on=TODAY)
        place_student(student=newcomer, section=next_a, on_date=next_year.start_date)
        r = self.client.post(f"{STRUCTURES}{structure.pk}/generate-one-time-invoice/", {"student": newcomer.pk})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["total"], "2500.00")

    def test_due_date_can_be_given(self):
        r = self.generate(self.ram, due_date=(TODAY + timedelta(days=30)).isoformat())
        self.assertEqual(r.data["due_date"], (TODAY + timedelta(days=30)).isoformat())
