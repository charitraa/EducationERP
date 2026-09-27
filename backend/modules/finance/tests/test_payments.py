"""Recording payments, receipts, and refunds."""
from datetime import timedelta
from decimal import Decimal as D

from django.utils import timezone

from ..models import Payment, Receipt, Refund
from .base import API, FinanceTestCase

PAYMENTS = f"{API}/payments/"


class PaymentTestCase(FinanceTestCase):
    def setUp(self):
        super().setUp()
        self.bill_term()
        self.login(self.office)
        self.invoice = self.invoice_of(self.ram)


class RecordPaymentTests(PaymentTestCase):
    def pay(self, **body):
        return self.client.post(PAYMENTS, {"invoice": self.invoice.pk, "method": "cash", **body})

    def test_a_full_payment_issues_a_receipt(self):
        r = self.pay(amount="10000")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["has_receipt"])
        self.invoice.refresh_from_db()
        self.assertEqual((self.invoice.paid_amount, self.invoice.is_paid), (D("10000.00"), True))
        receipt = Receipt.objects.get(payment_id=r.data["id"])
        self.assertTrue(receipt.receipt_number.startswith("RC-"))

    def test_partial_payments_accumulate(self):
        self.pay(amount="4000")
        self.pay(amount="4000")
        self.invoice.refresh_from_db()
        self.assertEqual((self.invoice.paid_amount, str(self.invoice.balance), self.invoice.is_paid),
                         (D("8000.00"), "2000.00", False))
        self.assertEqual(Receipt.objects.filter(payment__invoice=self.invoice).count(), 2)

    def test_cannot_overpay(self):
        r = self.pay(amount="10001")
        self.assertError(r, 409, "exceeds_balance")

    def test_amount_must_be_positive(self):
        self.assertEqual(self.pay(amount="0").status_code, 400)
        self.assertEqual(self.pay(amount="-5").status_code, 400)

    def test_paid_at_cannot_be_in_the_future(self):
        future = (timezone.now() + timedelta(days=2)).isoformat()
        self.assertEqual(self.pay(amount="100", paid_at=future).status_code, 400)

    def test_cannot_pay_a_cancelled_invoice(self):
        self.client.post(f"{API}/invoices/{self.invoice.pk}/cancel/", {"reason": "Withdrew"})
        self.assertError(self.pay(amount="100"), 409, "not_issued")

    def test_a_teacher_cannot_record_payments_but_a_cashier_can(self):
        from tests.factories import user_with_system_role

        teacher = user_with_system_role(self.org, "staff", email="teacher@kmc.test")
        self.login(teacher)
        self.assertEqual(self.pay(amount="100").status_code, 403)

    def test_only_the_office_may_refund_but_anyone_with_view_can_read(self):
        r = self.pay(amount="1000")
        payment_id = r.data["id"]
        from tests.factories import user_with_permissions

        cashier = user_with_permissions(self.org, ["finance.collect", "finance.view"], email="cashier@kmc.test")
        self.login(cashier)
        self.assertEqual(self.client.get(f"{PAYMENTS}{payment_id}/").status_code, 200)
        self.assertEqual(self.client.post(f"{PAYMENTS}{payment_id}/refund/", {"amount": "500", "reason": "x"}).status_code, 403)

    def test_list_and_filter_by_invoice(self):
        self.pay(amount="1000")
        r = self.client.get(PAYMENTS, {"invoice": self.invoice.pk})
        self.assertEqual(r.data["count"], 1)


class ReceiptTests(PaymentTestCase):
    def test_receipt_shows_the_payment_and_student(self):
        r = self.client.post(PAYMENTS, {"invoice": self.invoice.pk, "amount": "2000", "method": "bank",
                                        "reference": "TXN123"})
        receipt_id = Receipt.objects.get(payment_id=r.data["id"]).pk
        rr = self.client.get(f"{API}/receipts/{receipt_id}/")
        self.assertEqual((rr.data["student_name"], rr.data["amount"], rr.data["method"], rr.data["invoice_number"]),
                         ("Ram Student", "2000.00", "bank", self.invoice.invoice_number))

    def test_receipts_can_be_searched_by_number(self):
        r = self.client.post(PAYMENTS, {"invoice": self.invoice.pk, "amount": "500"})
        receipt = Receipt.objects.get(payment_id=r.data["id"])
        found = self.client.get(f"{API}/receipts/", {"search": receipt.receipt_number})
        self.assertEqual(found.data["count"], 1)


class RefundTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        r = self.client.post(PAYMENTS, {"invoice": self.invoice.pk, "amount": "5000", "method": "cash"})
        self.payment_id = r.data["id"]

    def refund(self, **body):
        return self.client.post(f"{PAYMENTS}{self.payment_id}/refund/", body)

    def test_a_full_refund(self):
        r = self.refund(amount="5000", reason="Wrong invoice")
        self.assertEqual(r.status_code, 201, r.data)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, D("0.00"))

    def test_a_partial_refund(self):
        self.refund(amount="2000", reason="Overpaid")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, D("3000.00"))

    def test_needs_a_reason(self):
        self.assertEqual(self.refund(amount="1000").status_code, 400)

    def test_cannot_refund_more_than_was_paid(self):
        r = self.refund(amount="5001", reason="x")
        self.assertError(r, 409, "exceeds_refundable")

    def test_cannot_refund_twice_over(self):
        self.refund(amount="4000", reason="First")
        r = self.refund(amount="4000", reason="Second")
        self.assertError(r, 409, "exceeds_refundable")
        self.assertEqual(r.data["error"]["details"]["refundable"], "1000.00")

    def test_a_refund_is_never_an_edit(self):
        self.refund(amount="1000", reason="Partial")
        self.assertEqual(Payment.objects.get(pk=self.payment_id).amount, D("5000.00"))  # original untouched
        self.assertEqual(Refund.objects.filter(payment_id=self.payment_id).count(), 1)

    def test_listed_and_readable(self):
        self.refund(amount="500", reason="x")
        self.assertEqual(self.client.get(f"{API}/refunds/", {"payment": self.payment_id}).data["count"], 1)
