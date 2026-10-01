from datetime import date, timedelta
from decimal import Decimal

from modules.attendance.models import StaffAttendanceDay
from modules.hr import services
from modules.hr.models import (
    Contract,
    EmployeeProfile,
    FiscalYear,
    LeaveBalance,
    LeaveRequest,
    LeaveType,
    Position,
    StaffDocument,
)
from modules.notifications.models import Notification
from tests.base import APITestCaseBase
from tests.factories import (
    create_calendar_event,
    create_campus,
    create_department,
    create_organization,
    create_staff_member,
    create_user,
    user_with_permissions,
    user_with_system_role,
)

API = "/api/v1/hr"


def next_weekday(iso_weekday, after=None):
    """The next date (after ``after``, default today) falling on ``iso_weekday``."""
    day = (after or date.today()) + timedelta(days=1)
    while day.isoweekday() != iso_weekday:
        day += timedelta(days=1)
    return day


class HRTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.branch = create_campus(self.org, code="branch")
        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test", campus=self.campus)
        self.branch_office = user_with_system_role(self.org, "campus-admin", email="branch@kmc.test",
                                                   campus=self.branch)
        self.hod_user = create_user(self.org, email="hod@kmc.test", user_type="staff")
        self.hod = create_staff_member(self.campus, employee_number="E-0", first_name="Hari", user=self.hod_user,
                                       joined_on=date(2015, 1, 1))
        self.teacher_user = create_user(self.org, email="sita@kmc.test", user_type="staff")
        self.teacher = create_staff_member(self.campus, employee_number="E-1", first_name="Sita", gender="female",
                                           user=self.teacher_user, joined_on=date(2020, 1, 1))
        self.other_user = create_user(self.org, email="ram@kmc.test", user_type="staff")
        self.other = create_staff_member(self.campus, employee_number="E-2", first_name="Ram", gender="male",
                                         user=self.other_user, joined_on=date(2020, 1, 1))
        self.branch_staff = create_staff_member(self.branch, employee_number="E-3", first_name="Gita")

        today = date.today()
        self.year = FiscalYear.objects.create(organization=self.org, name="FY now", start_date=today - timedelta(days=200),
                                              end_date=today + timedelta(days=165))
        self.casual = LeaveType.objects.create(organization=self.org, code="casual", name="Casual", annual_quota=12,
                                               carry_forward_max=5)
        self.unpaid = LeaveType.objects.create(organization=self.org, code="unpaid", name="Unpaid", is_paid=False)
        self.maternity = LeaveType.objects.create(organization=self.org, code="maternity", name="Maternity",
                                                  annual_quota=98, gender="female")

    def apply(self, user, **body):
        self.authenticate(user)
        body = {"leave_type": self.casual.pk, **body}
        for key in ("start_date", "end_date"):
            if isinstance(body.get(key), date):
                body[key] = body[key].isoformat()
        return self.client.post(f"{API}/leave-requests/me/", body, format="json")


class StructureTests(HRTestCase):
    def test_positions_and_contracts(self):
        self.authenticate(self.office)
        response = self.client.post(f"{API}/positions/", {"code": "Lecturer", "name": "Lecturer"})
        self.assertEqual(response.status_code, 201, response.data)
        position = response.data["id"]
        dept = create_department(self.org, code="acc", name="Accounts")
        response = self.client.post(f"{API}/contracts/", {
            "staff": self.teacher.pk, "kind": "probation", "position": position, "department": dept.pk,
            "start_date": "2020-01-01", "end_date": "2020-12-31", "probation_ends_on": "2020-06-30"})
        self.assertEqual(response.status_code, 201, response.data)
        # A second contract can't overlap the first.
        response = self.client.post(f"{API}/contracts/", {
            "staff": self.teacher.pk, "kind": "permanent", "start_date": "2020-12-01"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("start_date", response.data["error"]["details"])
        response = self.client.post(f"{API}/contracts/", {
            "staff": self.teacher.pk, "kind": "permanent", "position": position, "start_date": "2021-01-01"})
        self.assertEqual(response.status_code, 201, response.data)
        current = services.current_contract(self.teacher)
        self.assertEqual(current.kind, "permanent")

        # Started contracts are history: end them, don't delete them.
        response = self.client.delete(f"{API}/contracts/{current.pk}/")
        self.assertEqual(response.status_code, 409)
        response = self.client.post(f"{API}/contracts/{current.pk}/end/", {"end_date": "2030-01-01",
                                                                          "reason": "Resigned"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["ended_reason"], "Resigned")
        # A position named by contracts can't be deleted.
        self.assertEqual(self.client.delete(f"{API}/positions/{position}/").status_code, 409)

        # The staff member sees their own contracts.
        self.authenticate(self.teacher_user)
        response = self.client.get(f"{API}/contracts/me/")
        self.assertEqual(len(response.data), 2)

    def test_contract_cannot_start_before_joining(self):
        self.authenticate(self.office)
        response = self.client.post(f"{API}/contracts/", {"staff": self.teacher.pk, "kind": "permanent",
                                                          "start_date": "2019-01-01"})
        self.assertEqual(response.status_code, 400)

    def test_campus_scoping(self):
        Contract.objects.create(organization=self.org, staff=self.branch_staff, kind="permanent",
                                start_date=date(2020, 1, 1))
        Contract.objects.create(organization=self.org, staff=self.teacher, kind="permanent",
                                start_date=date(2020, 1, 1))
        self.authenticate(self.office)
        response = self.client.get(f"{API}/contracts/")
        self.assertEqual([row["staff"] for row in response.data["results"]], [self.teacher.pk])
        # Refused before validation, so the reply can't describe the other
        # campus's records (here: the overlapping contract's dates).
        self.authenticate(self.branch_office)
        response = self.client.post(f"{API}/contracts/", {"staff": self.teacher.pk, "kind": "contract",
                                                          "start_date": "2031-01-01"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("2020", str(response.data))
        self.authenticate(self.office)
        newcomer = create_staff_member(self.branch, employee_number="E-8", first_name="Hira")
        response = self.client.post(f"{API}/contracts/", {"staff": newcomer.pk, "kind": "permanent",
                                                          "start_date": "2031-01-01"})
        self.assertEqual(response.status_code, 403)

    def test_profile_and_documents(self):
        self.authenticate(self.office)
        response = self.client.post(f"{API}/profiles/", {"staff": self.teacher.pk, "pan_number": "123456789",
                                                         "tax_status": "married", "bank_name": "NIC Asia",
                                                         "bank_account_number": "0012"})
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(f"{API}/profiles/", {"staff": self.teacher.pk})
        self.assertEqual(response.status_code, 400)
        profile_id = EmployeeProfile.objects.get(staff=self.teacher).pk
        response = self.client.patch(f"{API}/profiles/{profile_id}/", {"staff": self.other.pk})
        self.assertEqual(response.status_code, 400)

        soon = (date.today() + timedelta(days=10)).isoformat()
        later = (date.today() + timedelta(days=400)).isoformat()
        for title, expires in (("Teaching license", soon), ("Passport", later)):
            response = self.client.post(f"{API}/documents/", {"staff": self.teacher.pk, "kind": "license",
                                                              "title": title, "expires_on": expires})
            self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(f"{API}/documents/", {"staff": self.teacher.pk, "kind": "pan", "title": "PAN",
                                                          "issued_on": "2020-01-02", "expires_on": "2020-01-01"})
        self.assertEqual(response.status_code, 400)
        response = self.client.get(f"{API}/documents/", {"expiring_within": 30})
        self.assertEqual([row["title"] for row in response.data["results"]], ["Teaching license"])

        self.authenticate(self.teacher_user)
        self.assertEqual(self.client.get(f"{API}/profiles/me/").data["pan_number"], "123456789")
        self.assertEqual(len(self.client.get(f"{API}/documents/me/").data), 2)
        # Staff can't read HR's records about others.
        self.assertEqual(self.client.get(f"{API}/profiles/").status_code, 403)
        self.authenticate(self.other_user)
        self.assertEqual(self.client.get(f"{API}/profiles/me/").status_code, 404)

    def test_fiscal_years_do_not_overlap(self):
        self.authenticate(self.office)
        response = self.client.post(f"{API}/fiscal-years/", {
            "name": "Overlap", "start_date": self.year.end_date.isoformat(),
            "end_date": (self.year.end_date + timedelta(days=364)).isoformat()})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f"{API}/fiscal-years/", {
            "name": "Next", "start_date": (self.year.end_date + timedelta(days=1)).isoformat(),
            "end_date": (self.year.end_date + timedelta(days=365)).isoformat()})
        self.assertEqual(response.status_code, 201, response.data)

    def test_leave_types_readable_by_staff(self):
        self.authenticate(self.teacher_user)
        response = self.client.get(f"{API}/leave-types/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)
        response = self.client.post(f"{API}/leave-types/", {"code": "x", "name": "X"})
        self.assertEqual(response.status_code, 403)


class LeaveTests(HRTestCase):
    def test_apply_counts_working_days_only(self):
        # Friday to Monday: Saturday is off (default Sunday–Friday week), and a
        # campus holiday on the Monday doesn't count either.
        friday = next_weekday(5)
        monday = friday + timedelta(days=3)
        create_calendar_event(self.org, title="Holiday", day=monday, kind="holiday", suspends_classes=True)
        response = self.apply(self.teacher_user, start_date=friday, end_date=monday, reason="Family")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["days"], "2.0")  # Friday and Sunday
        self.assertEqual(response.data["status"], "pending")

        # Approvers for the campus are told; the applicant isn't.
        self.assertTrue(Notification.objects.filter(recipient=self.office, event_type="LeaveRequested").exists())
        self.assertFalse(Notification.objects.filter(recipient=self.branch_office).exists())

    def test_only_holidays_is_refused(self):
        saturday = next_weekday(6)
        response = self.apply(self.teacher_user, start_date=saturday, end_date=saturday)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "no_working_days")

    def test_half_day(self):
        day = next_weekday(1)
        response = self.apply(self.teacher_user, start_date=day, end_date=day, half_day=True)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["days"], "0.5")
        response = self.apply(self.teacher_user, start_date=day, end_date=day + timedelta(days=1), half_day=True)
        self.assertEqual(response.status_code, 400)

    def test_balance_limits_and_pending_counts(self):
        start = next_weekday(7)  # a Sunday: Sunday–Friday is six working days
        response = self.apply(self.teacher_user, start_date=start, end_date=start + timedelta(days=5))
        self.assertEqual(response.data["days"], "6.0")
        # Six more still fit in the 12, the pending six counted.
        second = start + timedelta(days=7)
        response = self.apply(self.teacher_user, start_date=second, end_date=second + timedelta(days=5))
        self.assertEqual(response.status_code, 201, response.data)
        third = second + timedelta(days=7)
        response = self.apply(self.teacher_user, start_date=third, end_date=third)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "insufficient_balance")
        # Unlimited types never run out.
        response = self.apply(self.teacher_user, leave_type=self.unpaid.pk, start_date=third,
                              end_date=third + timedelta(days=20))
        self.assertEqual(response.status_code, 201, response.data)

    def test_overlap_is_refused(self):
        day = next_weekday(2)
        self.assertEqual(self.apply(self.teacher_user, start_date=day, end_date=day).status_code, 201)
        response = self.apply(self.teacher_user, leave_type=self.unpaid.pk, start_date=day - timedelta(days=1),
                              end_date=day)
        self.assertEqual(response.status_code, 409)

    def test_eligibility_and_years(self):
        day = next_weekday(2)
        response = self.apply(self.other_user, leave_type=self.maternity.pk, start_date=day, end_date=day)
        self.assertEqual(response.data["error"]["code"], "not_eligible")
        end = self.year.end_date
        response = self.apply(self.teacher_user, leave_type=self.unpaid.pk, start_date=end - timedelta(days=2),
                              end_date=end + timedelta(days=3))
        self.assertEqual(response.data["error"]["code"], "spans_fiscal_years")
        far = end + timedelta(days=30)
        response = self.apply(self.teacher_user, leave_type=self.unpaid.pk, start_date=far, end_date=far)
        self.assertEqual(response.data["error"]["code"], "no_fiscal_year")

    def test_staff_who_left_cannot_take_leave_after(self):
        self.other.status, self.other.left_on = "left", date.today() + timedelta(days=3)
        self.other.save()
        day = next_weekday(1, after=date.today() + timedelta(days=4))
        response = self.apply(self.other_user, start_date=day, end_date=day)
        self.assertEqual(response.data["error"]["code"], "not_employed")

    def test_approve_updates_balance_and_attendance(self):
        start = next_weekday(1)
        leave_id = self.apply(self.teacher_user, start_date=start, end_date=start + timedelta(days=1)).data["id"]
        self.authenticate(self.office)
        response = self.client.get(f"{API}/leave-requests/pending/")
        self.assertEqual([row["id"] for row in response.data["results"]], [leave_id])
        response = self.client.post(f"{API}/leave-requests/{leave_id}/approve/", {"note": "Enjoy"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "approved")
        balance = LeaveBalance.objects.get(staff=self.teacher, leave_type=self.casual)
        self.assertEqual(balance.used, Decimal("2"))
        days = StaffAttendanceDay.objects.filter(staff=self.teacher).order_by("date")
        self.assertEqual([(d.date, d.status, d.is_override) for d in days],
                         [(start, "leave", True), (start + timedelta(days=1), "leave", True)])
        self.assertTrue(Notification.objects.filter(recipient=self.teacher_user, event_type="LeaveApproved").exists())
        # Decided once.
        response = self.client.post(f"{API}/leave-requests/{leave_id}/reject/", {"note": "No"})
        self.assertEqual(response.status_code, 409)

        # The staff member sees it all.
        self.authenticate(self.teacher_user)
        balances = {row["leave_type_name"]: row for row in self.client.get(f"{API}/leave-balances/me/").data}
        self.assertEqual(balances["Casual"]["used"], "2.0")
        self.assertEqual(balances["Casual"]["available"], "10.0")
        self.assertIn("Maternity", balances)  # she is eligible

    def test_cancel_approved_leave_restores_balance_and_attendance(self):
        start = next_weekday(1)
        leave_id = self.apply(self.teacher_user, start_date=start, end_date=start).data["id"]
        self.authenticate(self.office)
        self.client.post(f"{API}/leave-requests/{leave_id}/approve/")
        # Future leave: the applicant may still call it off.
        self.authenticate(self.teacher_user)
        response = self.client.post(f"{API}/leave-requests/{leave_id}/cancel/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(LeaveBalance.objects.get(staff=self.teacher, leave_type=self.casual).used, 0)
        self.assertFalse(StaffAttendanceDay.objects.filter(staff=self.teacher).exists())
        self.assertEqual(self.client.post(f"{API}/leave-requests/{leave_id}/cancel/").status_code, 409)

    def test_started_leave_is_cancelled_only_by_hr(self):
        yesterday = date.today() - timedelta(days=1)
        leave = services.apply_leave(staff=self.teacher, leave_type=self.unpaid, start_date=yesterday,
                                     end_date=yesterday + timedelta(days=3), by=self.teacher_user)
        services.approve_leave(leave, by=self.office)
        self.authenticate(self.teacher_user)
        response = self.client.post(f"{API}/leave-requests/{leave.pk}/cancel/")
        self.assertEqual(response.status_code, 403)
        # Someone else's request looks like a missing one.
        self.authenticate(self.other_user)
        self.assertEqual(self.client.post(f"{API}/leave-requests/{leave.pk}/cancel/").status_code, 404)
        self.authenticate(self.office)
        self.assertEqual(self.client.post(f"{API}/leave-requests/{leave.pk}/cancel/").status_code, 200)

    def test_cannot_decide_own_request_and_campus_scope(self):
        day = next_weekday(3)
        leave = services.apply_leave(staff=self.hod, leave_type=self.casual, start_date=day, end_date=day)
        approver = user_with_permissions(self.org, ["hr.view", "hr.approve_leave"], email="head@kmc.test")
        self.hod.user = approver
        self.hod.save()
        self.authenticate(approver)
        response = self.client.post(f"{API}/leave-requests/{leave.pk}/approve/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "own_request")

        branch_leave = services.apply_leave(staff=self.branch_staff, leave_type=self.unpaid, start_date=day,
                                            end_date=day)
        self.authenticate(self.office)  # campus-admin of the main campus only
        self.assertEqual(self.client.post(f"{API}/leave-requests/{branch_leave.pk}/approve/").status_code, 404)
        self.authenticate(self.branch_office)
        self.assertEqual(self.client.post(f"{API}/leave-requests/{branch_leave.pk}/approve/").status_code, 200)

    def test_reject_needs_a_note(self):
        day = next_weekday(3)
        leave = services.apply_leave(staff=self.teacher, leave_type=self.casual, start_date=day, end_date=day)
        self.authenticate(self.office)
        self.assertEqual(self.client.post(f"{API}/leave-requests/{leave.pk}/reject/").status_code, 400)
        response = self.client.post(f"{API}/leave-requests/{leave.pk}/reject/", {"note": "Exams that week"})
        self.assertEqual(response.data["status"], "rejected")
        self.assertEqual(LeaveBalance.objects.get(staff=self.teacher, leave_type=self.casual).used, 0)

    def test_hr_applies_on_behalf(self):
        day = next_weekday(4)
        self.authenticate(self.office)
        response = self.client.post(f"{API}/leave-requests/", {"staff": self.other.pk, "leave_type": self.casual.pk,
                                                               "start_date": day.isoformat(),
                                                               "end_date": day.isoformat()})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["applied_by"], self.office.pk)
        response = self.client.post(f"{API}/leave-requests/", {
            "staff": self.branch_staff.pk, "leave_type": self.casual.pk, "start_date": day.isoformat(),
            "end_date": day.isoformat()})
        self.assertEqual(response.status_code, 403)
        # Staff without hr.manage can't.
        self.authenticate(self.teacher_user)
        response = self.client.post(f"{API}/leave-requests/", {"staff": self.other.pk, "leave_type": self.casual.pk,
                                                               "start_date": day.isoformat(),
                                                               "end_date": day.isoformat()})
        self.assertEqual(response.status_code, 403)

    def test_approving_after_balance_cut_is_refused(self):
        start = next_weekday(7)
        leave = services.apply_leave(staff=self.teacher, leave_type=self.casual, start_date=start,
                                     end_date=start + timedelta(days=5))
        balance = LeaveBalance.objects.get(staff=self.teacher, leave_type=self.casual)
        services.adjust_balance(balance, delta=Decimal("-8"), reason="Correction")
        with self.assertRaises(Exception) as caught:
            services.approve_leave(leave, by=self.office)
        self.assertEqual(caught.exception.code, "insufficient_balance")


class BalanceTests(HRTestCase):
    def test_proration_and_carry_forward(self):
        previous = FiscalYear.objects.create(organization=self.org, name="FY before",
                                             start_date=self.year.start_date - timedelta(days=365),
                                             end_date=self.year.start_date - timedelta(days=1))
        old = LeaveBalance.objects.create(organization=self.org, staff=self.teacher, leave_type=self.casual,
                                          fiscal_year=previous, entitled=12, used=3)
        self.assertEqual(old.total - old.used, 9)
        # A joiner halfway through the year gets about half the quota.
        joiner = create_staff_member(self.campus, employee_number="E-9", first_name="New",
                                     joined_on=self.year.start_date + timedelta(days=self.year.days // 2))

        self.authenticate(self.office)
        response = self.client.post(f"{API}/leave-balances/open/", {"fiscal_year": self.year.pk})
        self.assertEqual(response.status_code, 200, response.data)
        teacher = LeaveBalance.objects.get(staff=self.teacher, leave_type=self.casual, fiscal_year=self.year)
        self.assertEqual((teacher.entitled, teacher.carried_forward), (12, 5))  # 9 unused, at most 5 carry
        newcomer = LeaveBalance.objects.get(staff=joiner, leave_type=self.casual, fiscal_year=self.year)
        self.assertEqual(newcomer.entitled, Decimal("6"))
        # Men get no maternity balance; running it again changes nothing.
        self.assertFalse(LeaveBalance.objects.filter(staff=self.other, leave_type=self.maternity).exists())
        again = self.client.post(f"{API}/leave-balances/open/", {"fiscal_year": self.year.pk}).data
        self.assertEqual(again["created"], 0)
        # Only this campus's staff for a campus-scoped HR office.
        self.assertFalse(LeaveBalance.objects.filter(staff=self.branch_staff).exists())

        response = self.client.post(f"{API}/leave-balances/{teacher.pk}/adjust/", {"delta": "1.5",
                                                                                   "reason": "Worked Saturday"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], "18.5")
        response = self.client.post(f"{API}/leave-balances/{teacher.pk}/adjust/", {"delta": "0.3", "reason": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post(f"{API}/leave-balances/", {}).status_code, 403)

    def test_in_use_setup_is_protected(self):
        day = next_weekday(2)
        services.apply_leave(staff=self.teacher, leave_type=self.casual, start_date=day, end_date=day)
        self.authenticate(self.office)
        self.assertEqual(self.client.delete(f"{API}/leave-types/{self.casual.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(f"{API}/fiscal-years/{self.year.pk}/").status_code, 409)
        response = self.client.patch(f"{API}/fiscal-years/{self.year.pk}/",
                                     {"end_date": (self.year.end_date + timedelta(days=1)).isoformat()})
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(f"{API}/leave-types/{self.casual.pk}/", {"is_paid": False})
        self.assertEqual(response.status_code, 400)


class TenantTests(HRTestCase):
    def test_other_organizations_records_are_invisible(self):
        other_org = create_organization(code="other")
        other_campus = create_campus(other_org, code="main")
        stranger = create_staff_member(other_campus, employee_number="X-1")
        other_type = LeaveType.objects.create(organization=other_org, code="casual", name="Casual")
        position = Position.objects.create(organization=other_org, code="lect", name="Lecturer")
        self.authenticate(self.office)
        response = self.client.post(f"{API}/contracts/", {"staff": stranger.pk, "kind": "permanent",
                                                          "start_date": "2030-01-01"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f"{API}/contracts/", {"staff": self.teacher.pk, "kind": "permanent",
                                                          "position": position.pk, "start_date": "2030-01-01"})
        self.assertEqual(response.status_code, 400)
        response = self.apply(self.teacher_user, leave_type=other_type.pk, start_date=next_weekday(1),
                              end_date=next_weekday(1))
        self.assertEqual(response.status_code, 400)
        document = StaffDocument.objects.create(organization=other_org, staff=stranger, kind="cv", title="CV")
        self.authenticate(self.office)
        self.assertEqual(self.client.get(f"{API}/documents/{document.pk}/").status_code, 404)
        self.assertEqual(LeaveRequest.objects.count(), 0)
