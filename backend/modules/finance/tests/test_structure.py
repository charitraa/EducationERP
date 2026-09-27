"""Fee categories, fee structures and scholarships: setup, not billing."""
from decimal import Decimal

from ..models import Scholarship
from .base import API, FinanceTestCase

CATEGORIES = f"{API}/fee-categories/"
STRUCTURES = f"{API}/fee-structures/"
SCHOLARSHIPS = f"{API}/scholarships/"


class FeeCategoryTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def test_crud_and_lowercase_code(self):
        r = self.client.post(CATEGORIES, {"code": "Transport", "name": "Transport"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["code"], "transport")
        self.assertEqual(self.client.post(CATEGORIES, {"code": "transport", "name": "Dup"}).status_code, 400)
        self.assertEqual(self.client.get(CATEGORIES).status_code, 200)
        self.assertEqual(self.client.patch(f"{CATEGORIES}{r.data['id']}/", {"description": "Bus"}).status_code, 200)
        self.assertEqual(self.client.delete(f"{CATEGORIES}{r.data['id']}/").status_code, 204)

    def test_cannot_delete_a_category_in_use(self):
        self.assertError(self.client.delete(f"{CATEGORIES}{self.tuition.pk}/"), 409, "in_use")

    def test_a_teacher_cannot_manage_categories(self):
        from tests.factories import user_with_system_role

        teacher = user_with_system_role(self.org, "staff", email="teacher@kmc.test")
        self.login(teacher)
        self.assertEqual(self.client.get(CATEGORIES).status_code, 403)
        self.assertEqual(self.client.post(CATEGORIES, {"code": "x", "name": "X"}).status_code, 403)


class FeeStructureTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def body(self, **extra):
        return {"program": self.program.pk, "level": 12, "academic_year": self.year.pk, "name": "Grade 12",
                "items": [{"category": self.tuition.pk, "amount": "12000", "frequency": "per_term"}], **extra}

    def test_create_with_items(self):
        r = self.client.post(STRUCTURES, self.body(), format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(len(r.data["items"]), 1)
        self.assertEqual(r.data["items"][0]["category_name"], "Tuition")

    def test_one_structure_per_program_level_year(self):
        r = self.client.post(STRUCTURES, {"program": self.program.pk, "level": 11, "academic_year": self.year.pk,
                                          "name": "Dup", "items": [{"category": self.tuition.pk, "amount": "1",
                                                                    "frequency": "per_term"}]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_level_must_exist_in_the_program(self):
        r = self.client.post(STRUCTURES, self.body(level=3), format="json")
        self.assertEqual(r.status_code, 400)

    def test_a_category_cannot_be_listed_twice(self):
        body = self.body(items=[{"category": self.tuition.pk, "amount": "1", "frequency": "per_term"},
                                {"category": self.tuition.pk, "amount": "2", "frequency": "per_term"}])
        self.assertEqual(self.client.post(STRUCTURES, body, format="json").status_code, 400)

    def test_amount_must_be_positive(self):
        body = self.body(items=[{"category": self.tuition.pk, "amount": "0", "frequency": "per_term"}])
        self.assertEqual(self.client.post(STRUCTURES, body, format="json").status_code, 400)

    def test_another_organizations_category_is_refused(self):
        from tests.factories import create_organization

        from ..models import FeeCategory

        other = FeeCategory.objects.create(organization=create_organization(code="other"), code="x", name="X")
        body = self.body(items=[{"category": other.pk, "amount": "1", "frequency": "per_term"}])
        self.assertEqual(self.client.post(STRUCTURES, body, format="json").status_code, 400)

    def test_items_can_be_replaced_before_any_invoice_exists(self):
        r = self.client.post(STRUCTURES, self.body(), format="json")
        r2 = self.client.patch(f"{STRUCTURES}{r.data['id']}/",
                               {"items": [{"category": self.tuition.pk, "amount": "15000",
                                          "frequency": "per_term"}]}, format="json")
        self.assertEqual((r2.status_code, len(r2.data["items"])), (200, 1))

    def test_items_are_locked_once_invoiced(self):
        self.bill_term()
        r = self.client.patch(f"{STRUCTURES}{self.structure.pk}/",
                              {"items": [{"category": self.tuition.pk, "amount": "1", "frequency": "per_term"}]},
                              format="json")
        self.assertEqual(r.status_code, 400)

    def test_cannot_delete_once_invoiced(self):
        self.bill_term()
        self.assertError(self.client.delete(f"{STRUCTURES}{self.structure.pk}/"), 409, "in_use")

    def test_deleting_an_unused_structure(self):
        r = self.client.post(STRUCTURES, self.body(), format="json")
        self.assertEqual(self.client.delete(f"{STRUCTURES}{r.data['id']}/").status_code, 204)


class ScholarshipTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def test_create_percentage_and_flat(self):
        r1 = self.client.post(SCHOLARSHIPS, {"name": "Merit", "kind": "percentage", "value": "50"})
        r2 = self.client.post(SCHOLARSHIPS, {"name": "Staff ward", "kind": "flat", "value": "3000"})
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))

    def test_a_percentage_cannot_exceed_100(self):
        r = self.client.post(SCHOLARSHIPS, {"name": "Bad", "kind": "percentage", "value": "150"})
        self.assertEqual(r.status_code, 400)

    def test_value_must_be_positive(self):
        r = self.client.post(SCHOLARSHIPS, {"name": "Bad", "kind": "flat", "value": "0"})
        self.assertEqual(r.status_code, 400)

    def test_can_be_narrowed_to_one_category(self):
        r = self.client.post(SCHOLARSHIPS, {"name": "Tuition only", "kind": "percentage", "value": "10",
                                            "category": self.tuition.pk})
        self.assertEqual(r.data["category_name"], "Tuition")

    def test_cannot_delete_a_scholarship_in_use(self):
        from ..models import Scholarship, StudentScholarship

        scholarship = Scholarship.objects.create(organization=self.org, name="M", kind="flat", value=Decimal("100"))
        StudentScholarship.objects.create(organization=self.org, student=self.ram, scholarship=scholarship,
                                          started_on=self.year.start_date)
        self.assertError(self.client.delete(f"{SCHOLARSHIPS}{scholarship.pk}/"), 409, "in_use")


class StudentScholarshipTests(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)
        self.scholarship = Scholarship.objects.create(organization=self.org, name="Merit", kind="percentage",
                                                       value=Decimal("50"))
        self.url = f"{API}/student-scholarships/"

    def test_grant_and_end(self):
        r = self.client.post(self.url, {"student": self.ram.pk, "scholarship": self.scholarship.pk,
                                        "started_on": self.year.start_date.isoformat(), "reason": "Top of class"})
        self.assertEqual(r.status_code, 201, r.data)
        grant_id = r.data["id"]

        r2 = self.client.post(f"{self.url}{grant_id}/end/", {"ended_on": (self.year.end_date).isoformat()})
        self.assertEqual(r2.status_code, 200, r2.data)
        self.assertIsNotNone(r2.data["ended_on"])
        self.assertError(self.client.post(f"{self.url}{grant_id}/end/", {"ended_on": self.year.end_date.isoformat()}),
                         409, "already_ended")

    def test_cannot_grant_the_same_scholarship_twice_while_open(self):
        body = {"student": self.ram.pk, "scholarship": self.scholarship.pk,
                "started_on": self.year.start_date.isoformat()}
        self.client.post(self.url, body)
        self.assertEqual(self.client.post(self.url, body).status_code, 400)

    def test_no_put_patch_or_delete(self):
        # Neither action is declared, so HasPermission's default-closed rule blocks it before
        # DRF ever gets to say the method itself isn't allowed (same order as everywhere else).
        r = self.client.post(self.url, {"student": self.ram.pk, "scholarship": self.scholarship.pk,
                                        "started_on": self.year.start_date.isoformat()})
        gid = r.data["id"]
        self.assertEqual(self.client.patch(f"{self.url}{gid}/", {"reason": "x"}).status_code, 403)
        self.assertEqual(self.client.delete(f"{self.url}{gid}/").status_code, 403)

    def test_campus_scoped(self):
        from tests.factories import user_with_system_role

        other_admin = user_with_system_role(self.org, "campus-admin", email="other@kmc.test",
                                            campus=self.other_campus)
        self.login(other_admin)
        self.assertEqual(self.client.get(self.url).data["count"], 0)
