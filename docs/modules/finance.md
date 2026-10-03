# Finance

Fee structures, invoices, scholarships, payments, receipts and refunds — one
fee policy for the whole organization, like the grading scales in examinations. Everything
follows the conventions in [`identity.md`](identity.md).

```
FeeStructure (per program + level + year)
  └─► FeeStructureItem (category, amount, one_time | per_term)
                                  │
                     generate-invoices / generate-one-time-invoice
                                  ▼
                              Invoice ──► InvoiceItem (fee, scholarship, discount, fine, adjustment)
                                  │              ▲
                                  │        Scholarship ◄── StudentScholarship (standing, dated)
                                  ├──► Installment (a due-dated schedule, same total)
                                  ▼
                              Payment ──► Receipt
                                  │
                                  ▼
                               Refund
```

Code: `backend/modules/finance/`. `services.py` generates invoices, takes
payments, refunds and assesses late fees; `selectors.py` renders statements
and reports as data.

---

## Fee structure: one policy, split by program, level and year

A **FeeStructure** belongs to one program, at one level, for one academic
year — the same shape as a curriculum entry or a grade scale
(examinations). Its **FeeStructureItem**s say what each **FeeCategory** (tuition,
admission, transport, …) costs and how often it's billed:

- `per_term`: billed every time `generate-invoices` runs for a term.
- `one_time`: billed once, ever, through a separate action
  (`generate-one-time-invoice`) — typically admission or registration, run
  once when a student is admitted rather than every term.

A structure's items are locked once any invoice has been generated from it
(`items` becomes read-only on further edits) — a rule change applies to the
*next* structure, not silently to bills already sent out.

## Standing scholarships, one-off discounts

A **Scholarship** (a percentage or a flat amount, optionally narrowed to one
category) is a template; a **StudentScholarship** grants it to a student from
a date, until dropped — history, like `academics.StudentElective`: dropping
one ends it rather than deleting the row (`POST
/student-scholarships/{id}/end/`).

Every time an invoice is generated, each of the student's active grants adds
a negative `InvoiceItem` (`kind=scholarship`). A category-scoped grant can't
reduce that category below zero; an organization-wide grant draws down
whatever's left after grants already applied, oldest grant first — so two
generous scholarships together never push a total negative, they just
overlap at zero. A one-off reduction the office adds by hand instead (a
single invoice, not a standing rule) is a `discount` line, added the same way
as a `fine` or a plain `adjustment` (`POST /invoices/{id}/add-item/`).

## An invoice's life

1. **Generated**, not created by hand: `POST
   /fee-structures/{id}/generate-invoices/` bills every student currently
   placed in a matching class, for one term — already-invoiced students are
   skipped, so running it again only bills whoever is new (a late admission,
   a transfer in). "Currently" is today, kept inside the term: billing ahead
   of the term uses its first day, billing after it its last. One-time items
   go through `generate-one-time-invoice` instead, refused a second time for
   the same student and structure, and refused (`not_placed`) unless the
   student is in, or placed ahead into, a class of the structure's program,
   level and year.
2. **Ad-hoc lines** (a discount, a fine, a correction) can be added while
   it's `issued`; a line can't take the total below what's already been paid
   — refund first.
3. **Installments** (`POST /invoices/{id}/installments/`) split the same
   total into a due-dated schedule, for reminders and late fees. They never
   change what's owed, and can't be reset once a payment exists.
4. **Payment** (`POST /payments/`) is recorded against the invoice as a
   whole, not against one installment; overpaying is refused. Every payment
   issues a numbered **Receipt** automatically. A **Refund** reverses some or
   all of one payment — appended, never an edit, the same rule the audit log
   itself follows and marks keep after publishing.
5. **Cancel** (`POST /invoices/{id}/cancel/`) needs a reason and is refused
   once any payment exists — refund it first, so a cancelled invoice never
   leaves money unaccounted for.

## Late fees: an explicit action, not a background job

`POST /invoices/assess-late-fees/` adds a flat amount or a percentage as a
`fine` line to every invoice overdue by more than `grace_days`, once each —
an invoice that already has a fine is skipped, so running it again (the next
morning, the next week) never fines the same invoice twice. Same as attendance: nothing here runs on a schedule the platform manages itself:
someone (a cron job the school sets up, or the office by hand) decides when
to run it.

## Reports

| Report | Answers |
|---|---|
| `GET /invoices/reports/student/?student=` | One student's invoices, what's paid, what's owed |
| `GET /invoices/reports/outstanding/` | Overdue invoices, oldest first, filterable by section or program |
| `GET /invoices/reports/collection/` | Payments received in a span, totalled by method — a day sheet |

## Visibility

| Who | Sees |
|---|---|
| The finance office (`finance.manage`) | Fee structures, scholarships; generate, cancel, adjust invoices; refund |
| A cashier (`finance.collect`) | Record payments only — no fee setup, no refunds |
| Anyone with `finance.view` | Read invoices, payments, receipts, refunds, reports |
| A student | `GET /invoices/me/` — their own statement, published or not (there's no "unpublished" state here; every issued invoice is visible to its student) |
| A parent | The same, for a linked child (`?student=` when there are several) |

## API surface

```text
GET    /api/v1/fee-categories/                                       CRUD
GET    /api/v1/fee-structures/                                       CRUD (items locked once invoiced)
POST   /api/v1/fee-structures/{id}/generate-invoices/                 one per placed student, idempotent
POST   /api/v1/fee-structures/{id}/generate-one-time-invoice/         one student, refused twice
GET    /api/v1/scholarships/                                         CRUD
GET    /api/v1/student-scholarships/                                 list/retrieve/create; POST {id}/end/
GET    /api/v1/invoices/                                              list/retrieve only (generated, not created)
POST   /api/v1/invoices/{id}/add-item/  cancel/  installments/
GET    /api/v1/invoices/me/                                           a student's/parent's own statement
GET    /api/v1/invoices/reports/student/  outstanding/  collection/
POST   /api/v1/invoices/assess-late-fees/
GET    /api/v1/payments/                                              list/retrieve; POST records one
POST   /api/v1/payments/{id}/refund/
GET    /api/v1/receipts/  refunds/                                    list/retrieve
```

## Not built (by choice, for now)

- **An online payment gateway.** Payments are recorded by hand for now
  (cash, bank, cheque, or an "online" method with a reference). A real
  gateway (eSewa, Khalti, a card processor) is an adapter away, the same
  shape as attendance's biometric devices: the core doesn't need to know which
  one a school uses.
- **A general ledger / accounting export.** Invoices and payments are the
  record; posting them into double-entry books is a later integration.
- **Per-student ad-hoc fee overrides at generation time** (a one-off
  different tuition for one student, distinct from a scholarship). Today,
  a scholarship or an ad-hoc invoice item covers the same need.
