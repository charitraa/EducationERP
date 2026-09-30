"""Fee rules: generating invoices, taking payments, refunds, late fees.

Views and serializers call these so a rule lives in one place.
"""
from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.audit.models import AuditLog
from core.audit.services import log
from core.common.exceptions import ConflictError, PermissionDeniedError, ServiceError
from core.organizations.models import Organization
from core.permissions.selectors import campus_ids_with_permission
from modules.academics.models import Section
from modules.students.models import Enrollment

from .models import (
    FeeStructure,
    Frequency,
    Installment,
    Invoice,
    InvoiceItem,
    InvoiceItemKind,
    InvoiceStatus,
    Payment,
    Receipt,
    Refund,
    ScholarshipKind,
    StudentScholarship,
)

MODULE = "finance"
ZERO = Decimal("0")
VIEW, COLLECT, MANAGE = "finance.view", "finance.collect", "finance.manage"


def holds(user, code: str, campus_id) -> bool:
    if user.is_superuser:
        return True
    campus_ids = campus_ids_with_permission(user, code)
    return campus_ids is None or campus_id in campus_ids


def ensure_campus_allowed(user, code: str, campus_id) -> None:
    if not holds(user, code, campus_id):
        raise PermissionDeniedError("Your role does not cover this campus for this action.",
                                    code="wrong_campus")


# ---------------------------------------------------------------------------
# Numbering: one counter per organization, serialized on the tenant row.
# ---------------------------------------------------------------------------
def _next_number(organization_id: int, prefix: str, model) -> str:
    with transaction.atomic():
        Organization.objects.select_for_update().get(pk=organization_id)
        count = model.objects.filter(organization_id=organization_id).count()
        return f"{prefix}{count + 1:06d}"


# ---------------------------------------------------------------------------
# Scholarships
# ---------------------------------------------------------------------------
def grant_scholarship(*, student, scholarship, started_on: Date, reason: str = "", by=None) -> StudentScholarship:
    return StudentScholarship.objects.create(
        organization_id=student.organization_id, student=student, scholarship=scholarship,
        started_on=started_on, reason=reason, granted_by=by,
    )


def end_scholarship(grant: StudentScholarship, ended_on: Date, *, by=None) -> StudentScholarship:
    if grant.ended_on is not None:
        raise ConflictError("This scholarship has already ended.", code="already_ended")
    if ended_on <= grant.started_on:
        raise ServiceError("Must end after it started.", code="bad_date")
    grant.ended_on = ended_on
    grant.save(update_fields=["ended_on", "updated_at"])
    log(AuditLog.Action.UPDATE, instance=grant, module=MODULE, actor=by,
        changes={"ended_on": {"before": None, "after": str(ended_on)}})
    return grant


# ---------------------------------------------------------------------------
# Generating invoices
# ---------------------------------------------------------------------------
def _scholarship_lines(student, category_totals: dict, on: Date, by=None) -> list[dict]:
    """Standing scholarships as negative item dicts, one per grant.

    A category-scoped grant can't reduce that category below zero; an
    organization-wide grant draws down the fee total that's left after
    grants already applied, in a stable order (oldest grant first) so the
    result doesn't depend on how the database happens to return rows.
    """
    fee_total = sum(category_totals.values(), ZERO)
    remaining_overall = fee_total
    remaining_by_category = dict(category_totals)
    lines = []
    grants = (StudentScholarship.objects.on(on).filter(student=student)
             .select_related("scholarship", "scholarship__category").order_by("started_on", "pk"))
    for grant in grants:
        scholarship = grant.scholarship
        if not scholarship.is_active:
            continue
        category_id = scholarship.category_id
        base = remaining_by_category.get(category_id, ZERO) if category_id else remaining_overall
        if base <= 0:
            continue
        raw = base * scholarship.value / 100 if scholarship.kind == ScholarshipKind.PERCENTAGE else scholarship.value
        amount = min(raw, base)
        if amount <= 0:
            continue
        if category_id:
            remaining_by_category[category_id] -= amount
        remaining_overall -= amount
        lines.append({"kind": InvoiceItemKind.SCHOLARSHIP, "description": f"Scholarship: {scholarship.name}",
                      "amount": -amount, "category_id": category_id, "scholarship": scholarship, "added_by": by})
    return lines


def _matching_sections(fee_structure: FeeStructure):
    return Section.objects.filter(organization_id=fee_structure.organization_id, program=fee_structure.program,
                                  level=fee_structure.level, academic_year=fee_structure.academic_year)


def generate_term_invoices(fee_structure: FeeStructure, term, *, by, section=None, due_date: Date | None = None) -> dict:
    """One invoice per student currently placed in a section that matches
    the fee structure's program, level and academic year, for its
    ``per_term`` items. Already-invoiced students (for this term) are
    skipped, so running it again only bills whoever is new."""
    if term.academic_year_id != fee_structure.academic_year_id:
        raise ServiceError("This term isn't in the fee structure's academic year.", code="wrong_year")
    items = list(fee_structure.items.filter(frequency=Frequency.PER_TERM).select_related("category"))
    if not items:
        raise ServiceError("This fee structure has no per-term items.", code="no_items")

    sections = _matching_sections(fee_structure)
    if section is not None:
        sections = sections.filter(pk=section.pk)
    # Who is in the class today, kept inside the term: billing ahead uses its
    # first day, billing afterwards its last. Not always the first day, or a
    # student who joins mid-term (a late admission, a transfer in) would
    # never be billed however often this runs.
    on = min(max(timezone.localdate(), term.start_date), term.end_date)
    enrollments = (Enrollment.objects.on(on).filter(section__in=sections)
                  .select_related("student", "campus"))
    already = set(Invoice.objects.filter(term=term, student_id__in=enrollments.values("student_id"))
                 .exclude(status=InvoiceStatus.CANCELLED).values_list("student_id", flat=True))

    created = skipped = 0
    with transaction.atomic():
        for enrollment in enrollments:
            if enrollment.student_id in already:
                skipped += 1
                continue
            category_totals = {i.category_id: i.amount for i in items}
            issue_date = timezone.localdate()
            invoice = Invoice.objects.create(
                organization_id=fee_structure.organization_id, campus=enrollment.campus, student=enrollment.student,
                enrollment=enrollment, fee_structure=fee_structure, academic_year=fee_structure.academic_year,
                term=term, invoice_number=_next_number(fee_structure.organization_id, "INV-", Invoice),
                status=InvoiceStatus.ISSUED, issue_date=issue_date,
                due_date=due_date or (issue_date + timedelta(days=15)),
            )
            InvoiceItem.objects.bulk_create([
                InvoiceItem(organization_id=invoice.organization_id, invoice=invoice, category=i.category,
                           kind=InvoiceItemKind.FEE, description=i.category.name, amount=i.amount)
                for i in items
            ])
            for line in _scholarship_lines(enrollment.student, category_totals, invoice.issue_date, by=by):
                InvoiceItem.objects.create(organization_id=invoice.organization_id, invoice=invoice, **line)
            invoice.total = sum(invoice.items.values_list("amount", flat=True), ZERO)
            invoice.save(update_fields=["total", "updated_at"])
            already.add(enrollment.student_id)
            created += 1
    return {"created": created, "skipped": skipped}


def generate_one_time_invoice(fee_structure: FeeStructure, student, *, by, due_date=None) -> Invoice:
    """A student's one-time charges (e.g. admission) from this fee
    structure. Refused a second time for the same student and structure."""
    items = list(fee_structure.items.filter(frequency=Frequency.ONE_TIME).select_related("category"))
    if not items:
        raise ServiceError("This fee structure has no one-time items.", code="no_items")
    if Invoice.objects.filter(fee_structure=fee_structure, student=student, term__isnull=True).exclude(
            status=InvoiceStatus.CANCELLED).exists():
        raise ConflictError("This student already has a one-time invoice from this fee structure.",
                            code="already_invoiced")
    # The student's class now, or one they're placed in ahead (an admission
    # billed before the year starts), of this structure's program, level and
    # year. Otherwise another program's fees could be billed to them.
    today = timezone.localdate()
    enrollment = (Enrollment.objects.filter(student=student, section__in=_matching_sections(fee_structure))
                  .filter(Q(ended_on__isnull=True) | Q(ended_on__gt=today))
                  .select_related("campus").order_by("started_on").first())
    if enrollment is None:
        raise ServiceError("This student isn't placed in a class this fee structure covers "
                           "(its program, level and academic year).", code="not_placed")
    with transaction.atomic():
        invoice = Invoice.objects.create(
            organization_id=fee_structure.organization_id, campus=enrollment.campus, student=student,
            enrollment=enrollment, fee_structure=fee_structure, academic_year=fee_structure.academic_year,
            term=None, invoice_number=_next_number(fee_structure.organization_id, "INV-", Invoice),
            status=InvoiceStatus.ISSUED, issue_date=timezone.localdate(),
            due_date=due_date or timezone.localdate(),
        )
        InvoiceItem.objects.bulk_create([
            InvoiceItem(organization_id=invoice.organization_id, invoice=invoice, category=i.category,
                       kind=InvoiceItemKind.FEE, description=i.category.name, amount=i.amount)
            for i in items
        ])
        category_totals = {i.category_id: i.amount for i in items}
        for line in _scholarship_lines(student, category_totals, invoice.issue_date, by=by):
            InvoiceItem.objects.create(organization_id=invoice.organization_id, invoice=invoice, **line)
        invoice.total = sum(invoice.items.values_list("amount", flat=True), ZERO)
        invoice.save(update_fields=["total", "updated_at"])
    return invoice


# ---------------------------------------------------------------------------
# Changing an invoice
# ---------------------------------------------------------------------------
def add_invoice_item(invoice: Invoice, *, kind: str, description: str, amount: Decimal, category=None,
                     by=None) -> InvoiceItem:
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status != InvoiceStatus.ISSUED:
            raise ConflictError("Only an issued invoice can be changed.", code="not_issued")
        new_total = invoice.total + amount
        if new_total < invoice.paid_amount:
            raise ConflictError("That would take the total below what's already been paid. Refund first.",
                                code="total_below_paid")
        item = InvoiceItem.objects.create(organization_id=invoice.organization_id, invoice=invoice, category=category,
                                          kind=kind, description=description, amount=amount, added_by=by)
        invoice.total = new_total
        invoice.save(update_fields=["total", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=invoice, module=MODULE, actor=by,
            changes={"total": {"before": str(invoice.total - amount), "after": str(invoice.total)}},
            metadata={"item": description, "amount": str(amount)})
    return item


def cancel_invoice(invoice: Invoice, reason: str, *, by=None) -> Invoice:
    if not reason.strip():
        raise ServiceError("Say why the invoice is being cancelled.", code="reason_required")
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status == InvoiceStatus.CANCELLED:
            raise ConflictError("Already cancelled.", code="already_cancelled")
        if invoice.paid_amount > 0:
            raise ConflictError("This invoice has payments against it. Refund them first.", code="has_payments")
        invoice.status = InvoiceStatus.CANCELLED
        invoice.cancelled_at, invoice.cancelled_reason = timezone.now(), reason.strip()
        invoice.save(update_fields=["status", "cancelled_at", "cancelled_reason", "updated_at"])
        log(AuditLog.Action.UPDATE, instance=invoice, module=MODULE, actor=by,
            changes={"status": {"before": "issued", "after": "cancelled"}}, metadata={"reason": reason.strip()})
    return invoice


@dataclass
class InstallmentInput:
    amount: Decimal
    due_date: Date


def set_installments(invoice: Invoice, plan: list[InstallmentInput], *, by=None) -> list[Installment]:
    if len(plan) < 2:
        raise ServiceError("Give at least two installments.", code="too_few")
    if invoice.paid_amount > 0:
        raise ConflictError("Payments already exist; the schedule can't change now.", code="has_payments")
    total = sum((p.amount for p in plan), ZERO)
    if total != invoice.total:
        raise ServiceError(f"The installments add up to {total}, not the invoice's {invoice.total}.",
                           code="totals_dont_match")
    with transaction.atomic():
        invoice.installments.all().delete()
        rows = Installment.objects.bulk_create([
            Installment(organization_id=invoice.organization_id, invoice=invoice, sequence=n, amount=p.amount,
                       due_date=p.due_date)
            for n, p in enumerate(plan, start=1)
        ])
    return rows


# ---------------------------------------------------------------------------
# Payments, receipts, refunds
# ---------------------------------------------------------------------------
def record_payment(invoice: Invoice, *, amount: Decimal, method: str, paid_at: datetime, reference: str = "",
                   note: str = "", by=None) -> Payment:
    if amount <= 0:
        raise ServiceError("Give an amount greater than zero.", code="bad_amount")
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.status != InvoiceStatus.ISSUED:
            raise ConflictError("Only an issued invoice can take a payment.", code="not_issued")
        if amount > invoice.balance:
            raise ConflictError(f"That's more than the {invoice.balance} still owed.", code="exceeds_balance",
                                details={"balance": str(invoice.balance)})
        payment = Payment.objects.create(
            organization_id=invoice.organization_id, campus_id=invoice.campus_id, invoice=invoice, amount=amount,
            method=method, reference=reference, note=note, paid_at=paid_at, received_by=by,
        )
        invoice.paid_amount = invoice.paid_amount + amount
        invoice.save(update_fields=["paid_amount", "updated_at"])
        Receipt.objects.create(
            organization_id=invoice.organization_id, payment=payment,
            receipt_number=_next_number(invoice.organization_id, "RC-", Receipt), issued_at=timezone.now(),
        )
        log(AuditLog.Action.CREATE, instance=payment, module=MODULE, actor=by,
            metadata={"invoice": invoice.invoice_number, "amount": str(amount)})
    _notify_payment_received(invoice, payment)
    return payment


def _notify_payment_received(invoice: Invoice, payment: Payment) -> None:
    """``PaymentReceived`` (claude.md section 26): tell the student and their
    guardians through the central Notification Service, not by writing into
    another module's tables directly."""
    from modules.notifications.services import notify
    from modules.parents.selectors import links_for_student

    recipients = [invoice.student.user] if invoice.student.user_id else []
    recipients += [link.parent.user for link in links_for_student(invoice.student) if link.parent.user_id]
    notify(recipients, event_type="finance.payment_received",
          title=f"Payment received: {invoice.invoice_number}",
          body=f"{payment.amount} received against invoice {invoice.invoice_number}.",
          data={"invoice": invoice.pk, "payment": payment.pk}, organization_id=invoice.organization_id)


def refund_payment(payment: Payment, *, amount: Decimal, reason: str, by=None) -> Refund:
    if not reason.strip():
        raise ServiceError("Say why it's being refunded.", code="reason_required")
    if amount <= 0:
        raise ServiceError("Give an amount greater than zero.", code="bad_amount")
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        invoice = Invoice.objects.select_for_update().get(pk=payment.invoice_id)
        if amount > payment.refundable_amount:
            raise ConflictError(f"Only {payment.refundable_amount} of this payment hasn't already been refunded.",
                                code="exceeds_refundable", details={"refundable": str(payment.refundable_amount)})
        refund = Refund.objects.create(
            organization_id=payment.organization_id, payment=payment, amount=amount, reason=reason.strip(),
            refunded_at=timezone.now(), refunded_by=by,
        )
        invoice.paid_amount = invoice.paid_amount - amount
        invoice.save(update_fields=["paid_amount", "updated_at"])
        log(AuditLog.Action.CREATE, instance=refund, module=MODULE, actor=by,
            metadata={"payment": payment.pk, "amount": str(amount), "reason": reason.strip()})
    return refund


# ---------------------------------------------------------------------------
# Late fees
# ---------------------------------------------------------------------------
def assess_late_fees(*, organization_id: int, campus=None, amount: Decimal | None = None,
                     percentage: Decimal | None = None, grace_days: int = 0, category=None,
                     as_of: Date | None = None, by=None) -> dict:
    """A flat amount or a percentage of the total, added once to every
    invoice that's overdue by more than ``grace_days`` and doesn't already
    have a fine on it — so running this again never fines the same invoice
    twice."""
    if (amount is None) == (percentage is None):
        raise ServiceError("Give either an amount or a percentage, not both.", code="bad_rule")
    as_of = as_of or timezone.localdate()
    cutoff = as_of - timedelta(days=grace_days)
    invoices = Invoice.objects.filter(organization_id=organization_id, status=InvoiceStatus.ISSUED,
                                      due_date__lt=cutoff).exclude(items__kind=InvoiceItemKind.FINE)
    if campus is not None:
        invoices = invoices.filter(campus=campus)
    fined = 0
    with transaction.atomic():
        for invoice in invoices.select_for_update():
            if invoice.balance <= 0:
                continue
            fine = amount if amount is not None else (invoice.total * percentage / 100)
            if fine <= 0:
                continue
            InvoiceItem.objects.create(organization_id=invoice.organization_id, invoice=invoice, category=category,
                                       kind=InvoiceItemKind.FINE, description="Late fee", amount=fine, added_by=by)
            invoice.total = invoice.total + fine
            invoice.save(update_fields=["total", "updated_at"])
            fined += 1
    return {"fined": fined}
