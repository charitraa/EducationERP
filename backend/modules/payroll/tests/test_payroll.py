from datetime import date, timedelta
from decimal import Decimal

from modules.attendance.models import StaffAttendanceDay
from modules.attendance.selectors import staff_working_days
from modules.hr import services as hr_services
from modules.hr.models import EmployeeProfile, FiscalYear, LeaveType
from modules.notifications.models import Notification
from modules.payroll import services
from modules.payroll.models import (
    PayComponent,
    PayrollAdjustment,
    PayrollRun,
    PayrollSettings,
    Payslip,
    SalaryStructure,
    SalaryStructureLine,
    StaffSalary,
    TaxScheme,
    TaxSlab,
)
from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_staff_member,
    create_user,
    user_with_permissions,
    user_with_system_role,
)

API = "/api/v1/payroll"
PAYROLL = ["payroll.view", "payroll.manage", "payroll.approve"]


def D(value) -> Decimal:
    return Decimal(str(value))


class PayrollTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.branch = create_campus(self.org, code="branch")
        self.accountant = user_with_permissions(self.org, PAYROLL, email="accounts@kmc.test")
        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test", campus=self.campus)

        self.sita_user = create_user(self.org, email="sita@kmc.test", user_type="staff")
        self.sita = create_staff_member(self.campus, employee_number="E-1", first_name="Sita", gender="female",
                                        user=self.sita_user, joined_on=date(2020, 1, 1))
        today = date.today()
        self.year = FiscalYear.objects.create(organization=self.org, name="FY now",
                                              start_date=today - timedelta(days=200),
                                              end_date=today + timedelta(days=165))
        self.start = today - timedelta(days=60)
        self.end = self.start + timedelta(days=29)

        self.da = PayComponent.objects.create(organization=self.org, code="da", name="Dearness allowance",
                                              kind="earning")
        self.transport = PayComponent.objects.create(organization=self.org, code="transport", name="Transport",
                                                     kind="earning", is_taxable=False, prorate_for_absence=False)
        self.pf = PayComponent.objects.create(organization=self.org, code="pf", name="Provident fund",
                                              kind="deduction", calculation="percent_of_basic", is_pre_tax=True)
        self.lecturer = SalaryStructure.objects.create(organization=self.org, code="lect-a", name="Lecturer A",
                                                       basic=50000)
        for component, value in ((self.da, 5000), (self.transport, 2000), (self.pf, 10)):
            SalaryStructureLine.objects.create(structure=self.lecturer, component=component, value=value)
        self.salary = services.assign_salary(staff=self.sita, structure=self.lecturer,
                                             effective_from=date(2020, 1, 1))
        self.scheme = TaxScheme.objects.create(organization=self.org, fiscal_year=self.year, tax_status="single")
        for sequence, (upto, rate) in enumerate(((500000, 1), (700000, 10), (1000000, 20), (None, 30)), 1):
            TaxSlab.objects.create(scheme=self.scheme, sequence=sequence, upto=upto, rate=rate)

    def working_days(self, staff=None):
        return len(staff_working_days(staff or self.sita, self.start, self.end, employed_only=False))

    def open_run(self, campus=None, **kwargs):
        return services.create_run(campus=campus or self.campus, name="Month", period_start=self.start,
                                   period_end=self.end, **kwargs)

    def slip(self, run, staff=None):
        services.compute_run(run)
        return Payslip.objects.get(run=run, staff=staff or self.sita)


class TaxTests(PayrollTestCase):
    def test_slabs(self):
        self.assertEqual(services.annual_tax(self.scheme, D(400000)), D(4000))
        self.assertEqual(services.annual_tax(self.scheme, D(600000)), D(15000))
        self.assertEqual(services.annual_tax(self.scheme, D(1200000)), D(5000 + 20000 + 60000 + 60000))

    def test_monthly_projection_one_offs_and_caps(self):
        tax, taxable = services.monthly_tax(self.scheme, self.sita, regular=D(55000), pre_tax=D(5000))
        self.assertEqual((tax, taxable), (D("1250.00"), D("50000.00")))
        # A 10 000 bonus is taxed at the marginal 10%, not projected ×12.
        tax, _ = services.monthly_tax(self.scheme, self.sita, regular=D(55000), one_off=D(10000), pre_tax=D(5000))
        self.assertEqual(tax, D("2250.00"))
        self.scheme.pre_tax_cap_annual = 30000
        tax, _ = services.monthly_tax(self.scheme, self.sita, regular=D(55000), pre_tax=D(5000))
        self.assertEqual(tax, D("1500.00"))
        self.scheme.female_rebate_percent = 10
        tax, _ = services.monthly_tax(self.scheme, self.sita, regular=D(55000), pre_tax=D(5000))
        self.assertEqual(tax, D("1350.00"))
        ram = create_staff_member(self.campus, employee_number="E-2", gender="male")
        tax, _ = services.monthly_tax(self.scheme, ram, regular=D(55000), pre_tax=D(5000))
        self.assertEqual(tax, D("1500.00"))

    def test_scheme_by_tax_status(self):
        married = TaxScheme.objects.create(organization=self.org, fiscal_year=self.year, tax_status="married")
        TaxSlab.objects.create(scheme=married, sequence=1, upto=None, rate=0)
        self.assertEqual(services.scheme_for(self.sita, self.year), self.scheme)
        EmployeeProfile.objects.create(organization=self.org, staff=self.sita, tax_status="married")
        self.assertEqual(services.scheme_for(self.sita, self.year), married)

    def test_scheme_api_validates_slabs(self):
        self.authenticate(self.accountant)
        body = {"fiscal_year": self.year.pk, "tax_status": "married", "name": "Couple",
                "slabs": [{"upto": "600000", "rate": "1"}, {"upto": "500000", "rate": "10"}, {"rate": "20"}]}
        self.assertEqual(self.client.post(f"{API}/tax-schemes/", body, format="json").status_code, 400)
        body["slabs"] = [{"upto": "600000", "rate": "1"}, {"upto": "800000", "rate": "10"}]
        self.assertEqual(self.client.post(f"{API}/tax-schemes/", body, format="json").status_code, 400)
        body["slabs"] = [{"upto": "600000", "rate": "1"}, {"upto": None, "rate": "10"}]
        response = self.client.post(f"{API}/tax-schemes/", body, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual([s["sequence"] for s in response.data["slabs"]], [1, 2])
        body["tax_status"] = "single"
        self.assertEqual(self.client.post(f"{API}/tax-schemes/", body, format="json").status_code, 400)


class SalaryTests(PayrollTestCase):
    def test_structure_api(self):
        self.authenticate(self.accountant)
        response = self.client.post(f"{API}/structures/", {
            "code": "office", "name": "Office assistant", "basic": "20000",
            "lines": [{"component": self.da.pk, "value": "3000"}, {"component": self.pf.pk, "value": "150"}]},
            format="json")
        self.assertEqual(response.status_code, 400)  # a percentage over 100
        response = self.client.post(f"{API}/structures/", {
            "code": "office", "name": "Office assistant", "basic": "20000",
            "lines": [{"component": self.da.pk, "value": "3000"}, {"component": self.da.pk, "value": "1"}]},
            format="json")
        self.assertEqual(response.status_code, 400)  # the same component twice
        response = self.client.post(f"{API}/structures/", {
            "code": "office", "name": "Office assistant", "basic": "20000",
            "lines": [{"component": self.da.pk, "value": "3000"}, {"component": self.pf.pk, "value": "10"}]},
            format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data["lines"]), 2)
        # In-use structures and components can't be deleted.
        self.assertEqual(self.client.delete(f"{API}/structures/{self.lecturer.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(f"{API}/components/{self.da.pk}/").status_code, 409)
        response = self.client.post(f"{API}/components/", {"code": "bonus", "name": "Bonus", "kind": "earning",
                                                           "is_pre_tax": True})
        self.assertEqual(response.status_code, 400)

    def test_new_salary_ends_the_old_and_removal_reopens_it(self):
        self.authenticate(self.accountant)
        senior = SalaryStructure.objects.create(organization=self.org, code="lect-b", name="Lecturer B", basic=60000)
        response = self.client.post(f"{API}/staff-salaries/", {
            "staff": self.sita.pk, "structure": senior.pk, "effective_from": "2025-01-01", "basic": "62000",
            "lines": [{"component": self.da.pk, "value": "7000"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.salary.refresh_from_db()
        self.assertEqual(self.salary.effective_to, date(2024, 12, 31))
        new = StaffSalary.objects.get(pk=response.data["id"])
        self.assertEqual(new.monthly_basic, 62000)
        values = {c.code: v for c, v in services.component_values(new)}
        self.assertEqual(values, {"da": 7000})  # the Lecturer B structure has no lines of its own

        response = self.client.post(f"{API}/staff-salaries/", {"staff": self.sita.pk, "structure": senior.pk,
                                                               "effective_from": "2024-06-01"}, format="json")
        self.assertEqual(response.status_code, 409)

        self.assertEqual(self.client.delete(f"{API}/staff-salaries/{self.salary.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(f"{API}/staff-salaries/{new.pk}/").status_code, 204)
        self.salary.refresh_from_db()
        self.assertIsNone(self.salary.effective_to)

    def test_used_salary_is_locked(self):
        self.slip(self.open_run())
        self.authenticate(self.accountant)
        response = self.client.patch(f"{API}/staff-salaries/{self.salary.pk}/", {"basic": "1"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.delete(f"{API}/staff-salaries/{self.salary.pk}/").status_code, 409)

    def test_percent_components_use_the_persons_basic(self):
        salary = services.assign_salary(staff=self.sita, structure=self.lecturer, effective_from=date(2025, 1, 1),
                                        basic=D(40000))
        values = {c.code: services.component_amount(c, v, salary.monthly_basic)
                  for c, v in services.component_values(salary)}
        self.assertEqual(values["pf"], D("4000.00"))


class RunTests(PayrollTestCase):
    def test_full_month(self):
        slip = self.slip(self.open_run())
        self.assertEqual(slip.basis_days, self.working_days())
        self.assertEqual((slip.gross_pay, slip.tax, slip.total_deductions, slip.net_pay),
                         (D("57000.00"), D("1250.00"), D("6250.00"), D("50750.00")))
        self.assertEqual(slip.taxable_income, D("50000.00"))
        self.assertEqual(slip.absent_days, 0)
        sources = sorted(line.source for line in slip.lines.all())
        self.assertEqual(sources, ["basic", "component", "component", "component", "tax"])

    def test_unpaid_leave_and_absence_are_deducted(self):
        unpaid = LeaveType.objects.create(organization=self.org, code="unpaid", name="Unpaid", is_paid=False)
        paid = LeaveType.objects.create(organization=self.org, code="sick", name="Sick", annual_quota=12)
        days = staff_working_days(self.sita, self.start, self.end)
        leave = hr_services.apply_leave(staff=self.sita, leave_type=unpaid, start_date=days[0], end_date=days[1])
        hr_services.approve_leave(leave, by=self.office)
        sick = hr_services.apply_leave(staff=self.sita, leave_type=paid, start_date=days[3], end_date=days[3])
        hr_services.approve_leave(sick, by=self.office)
        StaffAttendanceDay.objects.create(organization=self.org, staff=self.sita, date=days[5], status="absent")
        StaffAttendanceDay.objects.create(organization=self.org, staff=self.sita, date=days[6], status="half_day")

        slip = self.slip(self.open_run())
        self.assertEqual((slip.unpaid_leave_days, slip.paid_leave_days, slip.absent_days),
                         (D(2), D(1), D(1)))  # half days aren't deducted by default
        per_day = D(55000) / self.working_days()
        deduction = (per_day * 3).quantize(D("0.01"))
        self.assertEqual(slip.gross_pay, D(57000) - deduction)
        self.assertIn(f"Unpaid days (3.0 of {self.working_days()})",
                      [line.description for line in slip.lines.all()])

        PayrollSettings.objects.create(organization=self.org, deduct_half_days=True)
        run = PayrollRun.objects.get()
        slip = self.slip(run)
        self.assertEqual(slip.absent_days, D("1.5"))

    def test_unrecorded_days_count_as_absent_when_configured(self):
        PayrollSettings.objects.create(organization=self.org, absence_basis="unrecorded")
        days = staff_working_days(self.sita, self.start, self.end)
        for day in days[:-3]:
            StaffAttendanceDay.objects.create(organization=self.org, staff=self.sita, date=day, status="present")
        slip = self.slip(self.open_run())
        self.assertEqual(slip.absent_days, 3)

        # Nobody punching in at all: nothing earned but the fixed allowance.
        ram = create_staff_member(self.campus, employee_number="E-2", first_name="Ram", joined_on=date(2020, 1, 1))
        services.assign_salary(staff=ram, structure=self.lecturer, effective_from=date(2020, 1, 1))
        run = PayrollRun.objects.get()
        slip = self.slip(run, ram)
        self.assertEqual(slip.gross_pay, D("2000.00"))
        self.assertEqual(slip.tax, 0)
        self.assertTrue(any("negative" in w for w in slip.details["warnings"]))

    def test_joiner_is_paid_for_days_employed(self):
        ram = create_staff_member(self.campus, employee_number="E-2", first_name="Ram",
                                  joined_on=self.start + timedelta(days=15))
        services.assign_salary(staff=ram, structure=self.lecturer, effective_from=ram.joined_on)
        slip = self.slip(self.open_run(), ram)
        before = len([d for d in staff_working_days(ram, self.start, self.end, employed_only=False)
                      if d < ram.joined_on])
        self.assertEqual(slip.not_employed_days, before)
        self.assertEqual(slip.working_days, self.working_days(ram) - before)

    def test_overtime_counted_and_overridden(self):
        days = staff_working_days(self.sita, self.start, self.end)
        StaffAttendanceDay.objects.create(organization=self.org, staff=self.sita, date=days[2], status="present",
                                          worked_minutes=600)
        StaffAttendanceDay.objects.create(organization=self.org, staff=self.sita, date=days[3],
                                          status="present", worked_minutes=490)  # 10 min: below the minimum
        run = self.open_run()
        slip = self.slip(run)
        self.assertEqual(slip.overtime_minutes, 120)
        hourly = D(50000) / self.working_days() / 480 * 60
        overtime = slip.lines.get(source="overtime")
        self.assertEqual(overtime.amount, (hourly * 2 * D("1.5")).quantize(D("0.01")))

        self.authenticate(self.accountant)
        response = self.client.post(f"{API}/payslips/{slip.pk}/set-overtime/", {"minutes": 240}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["number"], slip.number)
        # The override survives recomputing the whole run.
        self.client.post(f"{API}/runs/{run.pk}/compute/")
        slip = Payslip.objects.get(run=run, staff=self.sita)
        self.assertEqual(slip.overtime_minutes_override, 240)
        self.assertEqual(slip.lines.get(source="overtime").amount, (hourly * 4 * D("1.5")).quantize(D("0.01")))
        response = self.client.post(f"{API}/payslips/{slip.pk}/set-overtime/", {"minutes": None}, format="json")
        self.assertIsNone(response.data["overtime_minutes_override"])

    def test_adjustments(self):
        self.authenticate(self.accountant)
        response = self.client.post(f"{API}/adjustments/", {"staff": self.sita.pk, "kind": "earning",
                                                            "amount": "10000", "description": "Dashain bonus"})
        self.assertEqual(response.status_code, 201, response.data)
        bonus = response.data["id"]
        run = self.open_run()
        slip = self.slip(run)
        self.assertEqual(slip.tax, D("2250.00"))  # the bonus at the marginal rate
        self.assertEqual(slip.net_pay, D("50750.00") + D(10000) - D(1000))
        self.assertEqual(PayrollAdjustment.objects.get(pk=bonus).payslip, slip)

        # Withdrawn from a draft: the payslip is worked out again without it.
        self.assertEqual(self.client.delete(f"{API}/adjustments/{bonus}/").status_code, 204)
        slip = Payslip.objects.get(run=run, staff=self.sita)
        self.assertEqual(slip.net_pay, D("50750.00"))

        # After approval, corrections are new rows that land on the next payslip.
        advance = self.client.post(f"{API}/adjustments/", {"staff": self.sita.pk, "kind": "deduction",
                                                           "amount": "3000", "description": "Advance"}).data["id"]
        services.compute_run(run)
        services.approve_run(run, by=self.accountant)
        self.assertEqual(self.client.delete(f"{API}/adjustments/{advance}/").status_code, 409)
        slip = Payslip.objects.get(run=run, staff=self.sita)
        response = self.client.post(f"{API}/adjustments/", {"staff": self.sita.pk, "kind": "earning",
                                                            "amount": "500", "description": "Arrears",
                                                            "corrects": slip.pk})
        self.assertEqual(response.status_code, 201, response.data)

    def test_correction_must_name_an_approved_payslip_of_the_same_person(self):
        slip = self.slip(self.open_run())
        self.authenticate(self.accountant)
        response = self.client.post(f"{API}/adjustments/", {"staff": self.sita.pk, "kind": "earning",
                                                            "amount": "500", "description": "x", "corrects": slip.pk})
        self.assertEqual(response.status_code, 400)

    def test_workflow(self):
        self.authenticate(self.accountant)
        body = {"campus": self.campus.pk, "name": "Baisakh", "period_start": self.start.isoformat(),
                "period_end": self.end.isoformat()}
        response = self.client.post(f"{API}/runs/", body)
        self.assertEqual(response.status_code, 201, response.data)
        run = response.data["id"]
        self.assertEqual(response.data["fiscal_year"], self.year.pk)
        self.assertEqual(self.client.post(f"{API}/runs/", body).status_code, 409)
        long = {**body, "period_end": (self.start + timedelta(days=40)).isoformat()}
        self.assertEqual(self.client.post(f"{API}/runs/", long).status_code, 400)

        self.assertEqual(self.client.post(f"{API}/runs/{run}/approve/").status_code, 409)
        ram = create_staff_member(self.campus, employee_number="E-2", first_name="Ram")
        response = self.client.post(f"{API}/runs/{run}/compute/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["payslips"], 1)
        self.assertEqual([s["staff"] for s in response.data["skipped"]], [ram.pk])
        self.assertEqual(response.data["run"]["totals"]["net_pay"], "50750.00")

        # Nothing for the staff member to see until approval.
        self.authenticate(self.sita_user)
        self.assertEqual(self.client.get(f"{API}/payslips/me/").data, [])
        self.assertEqual(self.client.get(f"{API}/payslips/").status_code, 403)

        self.authenticate(self.accountant)
        response = self.client.post(f"{API}/runs/{run}/approve/")
        self.assertEqual(response.data["status"], "approved")
        self.assertTrue(Notification.objects.filter(recipient=self.sita_user, event_type="PayslipIssued").exists())
        self.assertEqual(self.client.post(f"{API}/runs/{run}/compute/").status_code, 409)
        self.assertEqual(self.client.post(f"{API}/runs/{run}/cancel/", {"reason": "x"}).status_code, 409)
        slip = Payslip.objects.get(run_id=run)
        self.assertEqual(self.client.post(f"{API}/payslips/{slip.pk}/set-overtime/", {"minutes": 1},
                                          format="json").status_code, 409)
        EmployeeProfile.objects.create(organization=self.org, staff=self.sita, bank_account_number="001")
        sheet = self.client.get(f"{API}/runs/{run}/bank-sheet/").data
        self.assertEqual([(r["account_number"], r["net_pay"]) for r in sheet["rows"]], [("001", "50750.00")])

        response = self.client.post(f"{API}/runs/{run}/mark-paid/", {"reference": "NIC-2083-01"})
        self.assertEqual(response.data["status"], "paid")
        self.authenticate(self.sita_user)
        mine = self.client.get(f"{API}/payslips/me/").data
        self.assertEqual([p["net_pay"] for p in mine], ["50750.00"])

    def test_cancel_draft_releases_adjustments(self):
        adjustment = PayrollAdjustment.objects.create(organization=self.org, staff=self.sita, kind="earning",
                                                      amount=100, description="x")
        run = self.open_run()
        services.compute_run(run)
        services.cancel_run(run, reason="Wrong dates")
        adjustment.refresh_from_db()
        self.assertIsNone(adjustment.payslip)
        # The period is free again.
        self.open_run()

    def test_nobody_is_paid_twice_for_a_period(self):
        run = self.open_run()
        services.compute_run(run)
        services.approve_run(run, by=self.accountant)
        self.sita.campus = self.branch
        self.sita.save()
        other = self.open_run(campus=self.branch)
        result = services.compute_run(other)
        self.assertEqual([(s["staff"], s["reason"]) for s in result["skipped"]], [(self.sita.pk, "already_paid")])

    def test_no_tax_scheme_warns(self):
        self.scheme.delete()
        slip = self.slip(self.open_run())
        self.assertEqual(slip.tax, 0)
        self.assertTrue(any("No tax scheme" in w for w in slip.details["warnings"]))


class AccessTests(PayrollTestCase):
    def test_campus_admin_has_no_payroll_access(self):
        run = self.open_run()
        self.authenticate(self.office)
        self.assertEqual(self.client.get(f"{API}/runs/").status_code, 403)
        self.assertEqual(self.client.get(f"{API}/staff-salaries/").status_code, 403)
        self.assertEqual(self.client.post(f"{API}/runs/{run.pk}/compute/").status_code, 403)

    def test_campus_scoped_payroll_role(self):
        from core.permissions.models import Role
        from tests.factories import create_role, grant

        branch_accountant = create_user(self.org, email="branch-accounts@kmc.test")
        role = create_role(self.org, code="branch-accounts", permissions=PAYROLL)
        grant(branch_accountant, Role.objects.get(pk=role.pk), campus=self.branch)
        run = self.open_run()
        self.authenticate(branch_accountant)
        self.assertEqual(self.client.get(f"{API}/runs/").data["count"], 0)
        self.assertEqual(self.client.post(f"{API}/runs/{run.pk}/compute/").status_code, 404)
        response = self.client.post(f"{API}/runs/", {"campus": self.campus.pk, "name": "x",
                                                     "period_start": (self.end + timedelta(days=1)).isoformat(),
                                                     "period_end": (self.end + timedelta(days=5)).isoformat()})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(f"{API}/staff-salaries/").data["count"], 0)

    def test_other_organizations_ids_are_refused(self):
        other = create_organization(code="other")
        other_campus = create_campus(other, code="main")
        stranger = create_staff_member(other_campus, employee_number="X")
        other_component = PayComponent.objects.create(organization=other, code="x", name="X", kind="earning")
        self.authenticate(self.accountant)
        response = self.client.post(f"{API}/structures/", {"code": "s", "name": "S", "basic": "1",
                                                           "lines": [{"component": other_component.pk,
                                                                      "value": "1"}]}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f"{API}/staff-salaries/", {"staff": stranger.pk, "structure": self.lecturer.pk,
                                                               "effective_from": "2030-01-01"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f"{API}/runs/", {"campus": other_campus.pk, "name": "x",
                                                     "period_start": "2030-01-01", "period_end": "2030-01-30"})
        self.assertEqual(response.status_code, 400)
