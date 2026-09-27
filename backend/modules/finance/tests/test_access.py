"""Who can see and do what: roles, campuses, organizations."""
from tests.factories import create_campus, create_organization, user_with_system_role

from .base import API, FinanceTestCase

STAFF_ENDPOINTS = ["fee-categories", "fee-structures", "scholarships", "student-scholarships", "invoices",
                   "payments", "receipts", "refunds"]


class AnonymousTests(FinanceTestCase):
    def test_every_endpoint_needs_a_login(self):
        for name in STAFF_ENDPOINTS + ["invoices/me"]:
            self.assertEqual(self.client.get(f"{API}/{name}/").status_code, 401, name)
        self.assertEqual(self.client.post(f"{API}/invoices/assess-late-fees/", {"amount": "1"}).status_code, 401)


class CampusScopeTests(FinanceTestCase):
    """A campus admin only reaches their own campus's invoices and payments."""

    def setUp(self):
        super().setUp()
        self.bill_term()
        self.invoice = self.invoice_of(self.ram)
        self.login(self.office)
        self.client.post(f"{API}/payments/", {"invoice": self.invoice.pk, "amount": "1000"})
        self.elsewhere = user_with_system_role(self.org, "campus-admin", email="other@kmc.test",
                                               campus=self.other_campus)

    def test_lists_are_empty_and_details_are_missing(self):
        self.login(self.elsewhere)
        for name in ("invoices", "payments", "receipts", "refunds"):
            self.assertEqual(self.client.get(f"{API}/{name}/").data["count"], 0, name)
        self.assertEqual(self.client.get(f"{API}/invoices/{self.invoice.pk}/").status_code, 404)

    def test_cannot_pay_or_cancel_another_campuss_invoice(self):
        self.login(self.elsewhere)
        # Same organization, so the serializer accepts the id; the write is still refused because
        # this role doesn't cover the invoice's campus.
        r = self.client.post(f"{API}/payments/", {"invoice": self.invoice.pk, "amount": "100"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.post(f"{API}/invoices/{self.invoice.pk}/cancel/", {"reason": "x"}).status_code,
                         404)

    def test_student_scholarships_are_campus_scoped_too(self):
        from ..models import Scholarship, StudentScholarship

        scholarship = Scholarship.objects.create(organization=self.org, name="M", kind="flat", value="100")
        StudentScholarship.objects.create(organization=self.org, student=self.ram, scholarship=scholarship,
                                          started_on=self.year.start_date)
        self.login(self.elsewhere)
        self.assertEqual(self.client.get(f"{API}/student-scholarships/").data["count"], 0)

    def test_an_organization_wide_admin_sees_every_campus(self):
        self.login(self.principal)
        self.assertEqual(self.client.get(f"{API}/invoices/").data["count"], 4)


class TenantTests(FinanceTestCase):
    """Another organization never sees, or can name, this one's records."""

    def setUp(self):
        super().setUp()
        self.bill_term()
        self.invoice = self.invoice_of(self.ram)
        self.rival_org = create_organization(code="rival")
        create_campus(self.rival_org, code="main")
        self.rival = user_with_system_role(self.rival_org, "org-admin", email="rival@rival.test")

    def test_nothing_is_visible(self):
        self.login(self.rival)
        for name in STAFF_ENDPOINTS:
            r = self.client.get(f"{API}/{name}/")
            self.assertEqual((r.status_code, r.data["count"]), (200, 0), name)
        self.assertEqual(self.client.get(f"{API}/invoices/{self.invoice.pk}/").status_code, 404)

    def test_cannot_target_another_organizations_campus_for_late_fees(self):
        self.login(self.rival)
        r = self.client.post(f"{API}/invoices/assess-late-fees/",
                             {"amount": "10", "campus": self.campus.pk})
        self.assertEqual(r.status_code, 400)
        self.assertIn("campus", r.data["error"]["details"])

    def test_cannot_pay_our_invoice_or_use_our_ids(self):
        self.login(self.rival)
        r = self.client.post(f"{API}/payments/", {"invoice": self.invoice.pk, "amount": "100"})
        self.assertEqual(r.status_code, 400)

        from tests.factories import create_academic_year, create_program

        year = create_academic_year(self.rival_org)
        program = create_program(self.rival_org, first_level=11, last_level=12)
        body = {"program": program.pk, "level": 11, "academic_year": year.pk, "name": "X"}
        for field, value in (("program", self.program.pk), ("academic_year", self.year.pk)):
            r = self.client.post(f"{API}/fee-structures/", {**body, field: value}, format="json")
            self.assertEqual(r.status_code, 400, field)


class NoPlainCreateTests(FinanceTestCase):
    """Invoices and admit-card-style records are only ever made by a
    generate action; a bare POST to the collection is refused."""

    def test_invoices_have_no_plain_create(self):
        from tests.factories import create_superuser

        self.login(self.office)
        self.assertEqual(self.client.post(f"{API}/invoices/", {}).status_code, 403)

        # A superuser bypasses HasPermission entirely; create() itself must still refuse.
        self.login(create_superuser(email="root@platform.test"))
        self.assertEqual(self.client.post(f"{API}/invoices/", {}).status_code, 405)


class RoleTests(FinanceTestCase):
    def test_view_only_role_cannot_write(self):
        from tests.factories import user_with_permissions

        viewer = user_with_permissions(self.org, ["finance.view"], email="viewer@kmc.test")
        self.login(viewer)
        self.assertEqual(self.client.get(f"{API}/invoices/").status_code, 200)
        self.assertEqual(self.client.post(f"{API}/fee-categories/", {"code": "x", "name": "X"}).status_code, 403)
        self.assertEqual(self.client.post(f"{API}/payments/", {"invoice": 1, "amount": "1"}).status_code, 403)

    def test_collect_only_role_can_pay_but_not_manage(self):
        from tests.factories import user_with_permissions

        self.bill_term()
        cashier = user_with_permissions(self.org, ["finance.collect"], email="cashier@kmc.test")
        self.login(cashier)
        r = self.client.post(f"{API}/payments/", {"invoice": self.invoice_of(self.ram).pk, "amount": "1000"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.client.get(f"{API}/invoices/").status_code, 403)  # no finance.view
        self.assertEqual(self.client.post(f"{API}/fee-categories/", {"code": "x", "name": "X"}).status_code, 403)
