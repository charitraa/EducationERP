# HR and Payroll

Who works here and on what terms, when they're away, and what they're paid
each month. Two modules, following the conventions in
[`identity.md`](identity.md): `backend/modules/hr/` and
`backend/modules/payroll/`. Student fees and staff pay never touch
each other.

```
                StaffMember ◄── EmployeeProfile (PAN, bank, tax status, fund numbers)
                    │   ▲
    Position ──► Contract ◄── academics.Department
                    │
                StaffDocument (number, expiry, link)

FiscalYear ──► LeaveBalance ◄── LeaveType (quota, carry-forward, paid?, gender)
    │               ▲ used
    └────────► LeaveRequest ── approve ──► attendance.set_staff_day("leave")

PayComponent ──► SalaryStructure (basic + lines) ──► StaffSalary (from a date; own basic/lines)
FiscalYear ──► TaxScheme (single | married) ──► TaxSlab…
PayrollRun (campus, period) ──► Payslip ──► PayslipLine
                                   ▲
                    PayrollAdjustment (one-off; corrects an approved payslip)
```

HR **extends** `staff.StaffMember`, which stays the employee record.
Two things HR needs were already built and are reused rather
than copied:

- **Department** is `academics.Department`. An office such as Accounts is a
  department with no programs. Inventory already issues stock to
  these departments.
- **Holiday** is the academic calendar's campus-wide closures
  (`CalendarEvent` with `suspends_classes`, no program). Attendance already
  reads them. Leave and payroll don't count those days either.

**Attendance** is `attendance.StaffAttendanceDay`. HR writes to it only
through `attendance.services.set_staff_day`, and payroll only reads it
through `attendance.selectors`.

---

## HR

### Positions and contracts

A **Position** is a structured job title ("Lecturer", "Accountant").
`StaffMember.designation` stays free text for anything more specific.

A **Contract** covers a person from `start_date` until `end_date` (left empty
when open-ended): its kind (permanent, probation, temporary, fixed-term,
part-time), position, department, probation end and notice period.

- Contracts are **history**. Only one covers a given day, and an overlapping
  contract is a 400.
- To change terms, **end** the old contract (`POST contracts/{id}/end/`,
  with a reason) and add a new one.
- A contract that has started can't be deleted (409 `started`). One that
  hasn't started yet can be.
- A contract can't start before the person joined.

### Profiles and documents

**EmployeeProfile** holds the data HR and payroll need beyond the
directory: PAN, `tax_status` (single or married, which picks the tax
slabs), bank name/branch/account, SSF/PF/CIT numbers, citizenship number
and an emergency contact. Each staff member has one.

**StaffDocument** records a document held: its kind, number, issuer, issue
and expiry dates. `?expiring_within=30` lists what needs renewing. Files
wait for platform file storage (`core/files/`, not built yet). Until then
`file_url` can point to where the scan is kept.

Staff see their own records through `contracts/me/`, `profiles/me/` and
`documents/me/`.

### Fiscal years

A **FiscalYear** is the leave year and payroll's tax year: in Nepal, 1
Shrawan to the end of Asar. It uses AD dates with a free-text BS name, like
academic years. Fiscal years can't overlap. Once leave is recorded in one,
its dates are fixed and it can't be deleted (409 `in_use`).

### Leave

**LeaveType**: casual, sick, maternity, unpaid, and so on.

| Field | Meaning |
|---|---|
| `annual_quota` | Days a year, in half days. Empty means unlimited: still counted, never refused |
| `carry_forward_max` | Unused days that move to the next year, at most this many |
| `prorate_for_joiners` | Someone joining (or leaving) mid-year gets a share of the quota, rounded down to a half day |
| `is_paid` | Unpaid leave is deducted by payroll. Fixed once the type has been used (400); add a new type instead |
| `allow_half_day`, `gender` | e.g. maternity for women only |

Every member of the organization can read the types, so staff know what to
apply for. Only HR can change them.

**LeaveBalance**: one per person, type and fiscal year. It holds `entitled`,
`carried_forward`, `adjustment` (by hand, with a reason in the audit log)
and `used`.

- `used` is kept current by approvals and cancellations, the way
  `Invoice.paid_amount` is.
- `available` = total − used − **pending** requests. A second request can't
  spend days a first one is still waiting on.
- Balances are created on first use. `POST leave-balances/open/` opens a
  whole year for everyone the caller covers, and is safe to rerun. The
  carry-forward is fixed when a balance is created.

**LeaveRequest**: `pending → approved | rejected`; `pending | approved →
cancelled`. Requests are never deleted.

- **Days are working days.** Each person's work schedule decides which
  weekdays count (Sunday–Friday by default), and campus closures don't
  count. A range that is only weekends or holidays is refused
  (`no_working_days`). A half day counts 0.5.
- **Refused up front:**
  - overlap with a pending or approved request (409 `overlaps`)
  - more than is available (`insufficient_balance`)
  - wrong gender (`not_eligible`)
  - outside the person's employment (`not_employed`)
  - crossing into the next fiscal year (`spans_fiscal_years`; apply for
    each year)
  - no fiscal year set up (`no_fiscal_year`)
- **Staff apply for themselves** with `POST leave-requests/me/`. HR can
  apply on someone's behalf with a plain `POST leave-requests/`.
- **Approvers** work from `leave-requests/pending/`, which needs only
  `hr.approve_leave`, so a head of department doesn't need to see all of HR.
  Approvers are campus-scoped like every other role, and they are notified
  (`LeaveRequested`) when someone at their campus applies.
- **Nobody decides their own request** (403 `own_request`).
- **A rejection needs a note.** The applicant is notified of the decision.
- **Approval writes attendance.** Each full day of approved leave becomes
  `leave` in staff attendance, through the attendance service, with the
  request's number in the note. A half day leaves the day to the punches,
  since the other half is worked.
- **Cancelling:**
  - The applicant can cancel while the request is pending or hasn't started.
  - HR or an approver can cancel any time.
  - Cancelling approved leave returns the days to the balance and clears
    the attendance days that approval set (only those, found by their note).
- **Cancelling leave that a payroll run has already paid** doesn't change
  that locked payslip. If pay needs correcting, it's a payroll adjustment
  (see below).

---

## Payroll

### Setting up pay

**PayComponent**: an allowance (earning) or a deduction.

- It is a fixed monthly amount or a percentage of basic.
- `is_taxable` applies to earnings. `is_pre_tax` applies to deductions
  taken before tax (PF, CIT, SSF); only deductions can be pre-tax.
- `prorate_for_absence` applies to earnings. Transport, say, may be paid in
  full whatever happens.
- Inactive components are left off new payslips.

**SalaryStructure**: a pay grade with a monthly `basic` plus component
lines, each a fixed amount or a percentage of at most 100.

**StaffSalary**: a structure assigned to one person from `effective_from`.

- `basic` and `lines` override the structure for that person only.
- Assigning a new salary **ends the previous one the day before**. It must
  start after the latest one on record (409 `not_after_current`).
- Once any payslip has used a salary, it can't be edited (400) or deleted
  (409). Assign a new one instead.
- Deleting an unused salary reopens the one it had ended, as if it had never
  been made. The latest salary must be deleted first.

**PayrollSettings**: one row per organization. Without one, the defaults
apply.

| Setting | Default | Meaning |
|---|---|---|
| `absence_basis` | `marked` | `marked`: only days marked absent are deducted. `unrecorded`: any past working day with no attendance counts as absent (for campuses where everyone punches in) |
| `deduct_half_days` | off | A `half_day` in attendance costs half a day's pay |
| `days_basis` | `working` | A day's pay is the monthly amount divided by the working days in the period, or by its calendar days |
| `overtime_enabled`, `overtime_multiplier`, `overtime_min_minutes` | on, 1.5, 30 | Overtime pay; extra time below the minimum on a day isn't counted |
| `default_day_minutes` | 480 | Day length for staff with no work schedule |

### Tax

A **TaxScheme** is one fiscal year's slabs for one tax status, single or
married.

- A **TaxSlab** taxes income up to `upto` at `rate`%. Limits must rise, and
  only the last slab is open-ended.
- `female_rebate_percent` comes off the tax of women employees.
- `pre_tax_cap_annual` and `pre_tax_cap_fraction` cap how much pre-tax
  deduction counts (e.g. Rs 5 lakh or one third).
- Nothing is hardcoded to one country's law. Nepal's slabs are data an
  organization enters, and the same goes for anywhere else.

How the tax for a month is worked out:

1. **Regular pay** is projected to a year (×12), less the pre-tax deductions
   allowed by the caps, and taxed by the slabs. A twelfth is withheld.
2. **One-off pay** (overtime and adjustments, such as a Dashain bonus or
   arrears) is taxed *on top of* that year at the marginal rate, all in that
   month. Projecting a bonus ×12 would overstate the tax.
3. The rebate comes off, and the result is rounded to paisa.
4. With no scheme for the year and status, no tax is withheld, and the
   payslip carries a warning saying so.

### Runs and payslips

A **PayrollRun** is one campus's pay period.

- The dates are explicit, so Nepali months (29–32 days) work. A period is
  at most 32 days and stays inside one fiscal year, which picks the tax
  schemes.
- Runs of the same campus can't overlap (409). A cancelled run frees its
  period.
- Status goes `draft → approved → paid`, and a draft can be cancelled.

**`compute`** works out a payslip for every staff member of the campus
employed during the period, from:

- **the salary**: the latest assignment in force during the period
- **approved leave**: paid leave is paid, unpaid leave is deducted
- **attendance**:
  - `absent` is deducted
  - `half_day` is deducted if the settings say so
  - `leave` set by hand with no request is paid, with a warning
  - worked minutes beyond the schedule, or any worked minutes on a day off,
    are overtime
- **employment**: days before joining or after leaving aren't paid
- **pending adjustments** for that person

Unpaid days are one line, a deduction from earnings that also lowers
taxable income:

> prorated earnings × unpaid days ÷ basis days

Overtime pays:

> basic ÷ basis days ÷ day length × hours × multiplier

People with no salary are skipped, and the response lists them with a
reason. So are people already on a payslip for an overlapping period, for
example after a transfer to another campus (`already_paid`).

A payslip keeps its working-out alongside the amounts:

- days worked, paid leave, unpaid leave, absent and not employed
- overtime minutes
- gross, taxable income, tax, total deductions and net
- the tax scheme used
- `details`: the day-by-day exceptions and any warnings (no tax scheme,
  negative net pay, …)

**While draft**:

- `compute` can run again. Adjustments and hand-set overtime carry over.
- `payslips/{id}/set-overtime/` overrides one person's overtime. Setting it
  to `null` goes back to the count from attendance.

**`approve`** (`payroll.approve`) needs a computed run, locks every
payslip and notifies staff (`PayslipIssued`). After that:

- compute and cancel answer 409 `not_draft`
- the salaries used can't be edited
- the tax schemes used can't be deleted

**`mark-paid`** records the payment reference and time. **`bank-sheet`**
lists each person's account and net pay, plus who has no bank account on
file.

Staff see their own payslips at `payslips/me/`, from approved or paid runs
only.

### Adjustments: corrections are new rows

A **PayrollAdjustment** is a one-off earning or deduction: arrears, a
bonus, a salary advance being recovered.

- The next `compute` of a draft run for that person picks it up.
- Until that payslip is approved, `DELETE` withdraws it, and a draft payslip
  carrying it is worked out again without it.
- After approval it's history (409 `locked`).
- A mistake on an approved payslip is never edited. It's corrected by a new
  adjustment with `corrects` pointing at that payslip, which must be an
  approved one of the same person. The correction lands on their next
  payslip.

---

## Permissions

| Code | Who | What |
|---|---|---|
| `hr.view` | HR staff, managers | contracts, profiles, documents, leave |
| `hr.manage` | the HR office | set all of that up; apply for leave on someone's behalf; adjust and open balances |
| `hr.approve_leave` | heads of department, principals | the pending queue; approve or reject |
| `payroll.view` | accounts | structures, salaries, runs, payslips |
| `payroll.manage` | accounts | components, structures, salaries, tax, settings, runs, overtime, adjustments, mark paid |
| `payroll.approve` | the approver | approve a computed run |

- `campus-admin` gets the three HR codes.
- **No shipped role below `org-admin` gets payroll.** Salaries are
  sensitive, so an organization grants payroll through its own role (an
  "Accountant"), organization-wide or for one campus.
- Self-service needs no permission: leave (`me`, cancel), balances,
  contracts, profile, documents and payslips. Being the staff member is the
  authorization.
- Everything about a person is **campus-scoped** through their campus, and
  payroll runs through theirs.
- A write naming a staff member at a campus the role doesn't cover is
  refused (403) before any other check. The reply can't describe that
  person's records, such as an overlapping contract's dates.

## API surface

```text
GET    /api/v1/hr/positions/  fiscal-years/  leave-types/             CRUD (HR); DELETE 409 while in use; leave types readable by all
GET    /api/v1/hr/contracts/  contracts/me/                            CRUD (HR); POST {id}/end/; DELETE only before it starts
GET    /api/v1/hr/profiles/  profiles/me/                              CRUD (HR)
GET    /api/v1/hr/documents/  documents/me/                            CRUD (HR); ?expiring_within=30
GET    /api/v1/hr/leave-balances/  leave-balances/me/                  POST open/ ; POST {id}/adjust/
GET    /api/v1/hr/leave-requests/  pending/                            POST (HR, on behalf); POST {id}/approve/  reject/  cancel/
GET    /api/v1/hr/leave-requests/me/                                   POST applies for oneself

GET    /api/v1/payroll/settings/  components/  structures/  tax-schemes/   CRUD (accounts); DELETE 409 while in use
GET    /api/v1/payroll/staff-salaries/                                 POST assigns (ends the previous); PATCH/DELETE while unused
GET    /api/v1/payroll/runs/                                           POST opens a period; {id}/compute/  approve/  mark-paid/  cancel/
GET    /api/v1/payroll/runs/{id}/bank-sheet/
GET    /api/v1/payroll/payslips/  payslips/me/                         POST {id}/set-overtime/ (draft)
GET    /api/v1/payroll/adjustments/                                    POST adds; DELETE withdraws until approved
```

## Tests

- **Unit tests:** 23 in `hr`, 22 in `payroll`, plus the cross-tenant sweep
  (`tests/test_tenant_sweep.py`). The sweep has a row of every new model,
  attacks for each action with ids in its body, and a nested-id test for
  salary lines.
- **Live run on a real server**, with a Kathmandu college's HR office and
  accounts working through Shrawan and Bhadra 2083:
  - seven staff across two campuses
  - Nepal's single and couple slabs with the 10% rebate for women and the
    PF/CIT cap
  - a mid-month joiner, paid and unpaid leave around a holiday, a marked
    absence, a long day's overtime, a Teej bonus, arrears correcting an
    approved payslip, and an advance recovery

  Every payslip figure was recomputed independently in the script and
  matched. All 101 HR and payroll operations were exercised: 824 checks,
  zero server errors, including anonymous access and replaying another
  college's record URLs.

## Not built (by choice, for now)

- **A cumulative (year-to-date) tax true-up.** TDS projects each month.
  Someone who joins mid-year, or whose pay changes, may be over- or
  under-withheld until year end. The payslips hold every figure needed for
  an annual reconciliation later.
- **Mid-period salary changes pro-rated.** The run uses the latest salary in
  force during the period. A raise mid-month is settled with an arrears
  adjustment.
- **PF/SSF on earned rather than full basic.** Percentage deductions use the
  month's full basic, even when unpaid days reduce earnings.
- **Employer contributions** (the employer's PF/SSF share, gratuity) and
  statutory returns or reports (SSF, IRD TDS filing).
- **Loans with repayment schedules.** An advance is recovered by repeated
  adjustments for now.
- **Payslip PDF**, like report cards: clients render the structured JSON.
- **Document file uploads** wait for `core/files/`. `file_url` links to the
  scan meanwhile.
- **Accounts payable** (paying suppliers, from inventory purchase orders)
  isn't payroll. It belongs with a later accounting module.
- **Leave encashment** at year end or on leaving, and **compensatory
  leave** for working a holiday. Both can be done with a balance adjustment
  and a payroll adjustment for now.
- **Substitutes when a teacher's leave is approved.** The timetable's lesson
  changes handle cover. Approval doesn't create them automatically.
