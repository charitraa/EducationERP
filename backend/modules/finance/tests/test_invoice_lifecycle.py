"""Ad-hoc items, cancelling, and splitting into installments."""
from datetime import timedelta
from decimal import Decimal as D

from .base import API, TODAY, FinanceTestCase

INVOICES = f"{API}/invoices/"


class InvoiceLifecycleTestCase(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.bill_term()
        self.login(self.office)
        self.invoice = self.invoice_of(self.ram)


class AddItemTests(InvoiceLifecycleTestCase):
    def url(self):
        return f"{INVOICES}{self.invoice.pk}/add-item/"

    def test_add_a_discount(self):
        r = self.client.post(self.url(), {"kind": "discount", "description": "Early payment", "amount": "500"})
        self.assertEqual(r.status_code, 201, r.data)
        self.invoice.refresh_from_db()
        self.assertEqual((r.data["amount"], self.invoice.total), ("-500.00", D("9500.00")))

    def test_add_a_fine(self):
        r = self.client.post(self.url(), {"kind": "fine", "description": "Late", "amount": "200"})
        self.assertEqual(r.status_code, 201, r.data)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.total, D("10200.00"))

    def test_a_fine_must_be_positive_a_discount_must_be_negative(self):
        self.assertEqual(self.client.post(self.url(), {"kind": "fine", "description": "x", "amount": "-1"}).status_code, 400)
        # A discount given as a positive number is flipped to negative automatically.
        r = self.client.post(self.url(), {"kind": "discount", "description": "x", "amount": "100"})
        self.assertEqual(r.data["amount"], "-100.00")

    def test_an_adjustment_can_go_either_way(self):
        r = self.client.post(self.url(), {"kind": "adjustment", "description": "Correction", "amount": "-250"})
        self.assertEqual(r.status_code, 201, r.data)

    def test_cannot_reduce_the_total_below_what_is_already_paid(self):
        from django.utils import timezone

        from .. import services

        services.record_payment(self.invoice, amount=D("9000"), method="cash", paid_at=timezone.now(), by=self.office)
        r = self.client.post(self.url(), {"kind": "discount", "description": "Too much", "amount": "5000"})
        self.assertError(r, 409, "total_below_paid")

    def test_a_cancelled_invoice_cannot_be_changed(self):
        self.client.post(f"{INVOICES}{self.invoice.pk}/cancel/", {"reason": "Withdrawn"})
        self.assertError(self.client.post(self.url(), {"kind": "fine", "description": "x", "amount": "1"}),
                         409, "not_issued")

    def test_a_category_can_be_named(self):
        r = self.client.post(self.url(), {"kind": "fine", "description": "Library fine", "amount": "50",
                                          "category": self.admission_fee.pk})
        self.assertEqual(r.data["category_name"], "Admission")


class CancelTests(InvoiceLifecycleTestCase):
    def test_cancel_with_a_reason(self):
        self.assertEqual(self.client.post(f"{INVOICES}{self.invoice.pk}/cancel/", {}).status_code, 400)
        r = self.client.post(f"{INVOICES}{self.invoice.pk}/cancel/", {"reason": "Student withdrew"})
        self.assertEqual((r.status_code, r.data["status"]), (200, "cancelled"))

    def test_cannot_cancel_twice(self):
        self.client.post(f"{INVOICES}{self.invoice.pk}/cancel/", {"reason": "x"})
        self.assertError(self.client.post(f"{INVOICES}{self.invoice.pk}/cancel/", {"reason": "y"}), 409,
                         "already_cancelled")

    def test_cannot_cancel_once_paid(self):
        from .. import services
        from django.utils import timezone

        services.record_payment(self.invoice, amount=D("1000"), method="cash", paid_at=timezone.now(), by=self.office)
        self.assertError(self.client.post(f"{INVOICES}{self.invoice.pk}/cancel/", {"reason": "x"}), 409,
                         "has_payments")

    def test_a_cancelled_invoice_regenerates_on_a_rerun(self):
        from ..models import Invoice

        self.client.post(f"{INVOICES}{self.invoice.pk}/cancel/", {"reason": "Withdrew, then came back"})
        result = self.bill_term()
        self.assertEqual(result["created"], 1)  # Ram gets a fresh invoice; the others were already billed
        self.assertEqual(Invoice.objects.filter(student=self.ram, term=self.term1).count(), 2)


class InstallmentTests(InvoiceLifecycleTestCase):
    def url(self):
        return f"{INVOICES}{self.invoice.pk}/installments/"

    def test_split_into_installments(self):
        r = self.client.post(self.url(), {"installments": [
            {"amount": "5000", "due_date": (TODAY + timedelta(days=15)).isoformat()},
            {"amount": "5000", "due_date": (TODAY + timedelta(days=45)).isoformat()},
        ]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(r.data["installments"]), 2)

    def test_must_add_up_to_the_total(self):
        r = self.client.post(self.url(), {"installments": [
            {"amount": "5000", "due_date": TODAY.isoformat()},
            {"amount": "4000", "due_date": TODAY.isoformat()},
        ]}, format="json")
        self.assertError(r, 400, "totals_dont_match")

    def test_needs_at_least_two(self):
        r = self.client.post(self.url(), {"installments": [{"amount": "10000", "due_date": TODAY.isoformat()}]},
                             format="json")
        self.assertError(r, 400, "too_few")

    def test_replacing_the_schedule(self):
        self.client.post(self.url(), {"installments": [
            {"amount": "5000", "due_date": TODAY.isoformat()}, {"amount": "5000", "due_date": TODAY.isoformat()}]},
            format="json")
        r = self.client.post(self.url(), {"installments": [
            {"amount": "3000", "due_date": TODAY.isoformat()}, {"amount": "7000", "due_date": TODAY.isoformat()}]},
            format="json")
        self.assertEqual([D(i["amount"]) for i in r.data["installments"]], [D("3000.00"), D("7000.00")])

    def test_cannot_change_the_schedule_once_a_payment_exists(self):
        from .. import services
        from django.utils import timezone

        services.record_payment(self.invoice, amount=D("1000"), method="cash", paid_at=timezone.now(), by=self.office)
        r = self.client.post(self.url(), {"installments": [
            {"amount": "5000", "due_date": TODAY.isoformat()}, {"amount": "5000", "due_date": TODAY.isoformat()}]},
            format="json")
        self.assertError(r, 409, "has_payments")
