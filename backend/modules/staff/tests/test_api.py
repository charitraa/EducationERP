from django.db import IntegrityError, transaction

from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_staff_member,
    create_user,
    user_with_permissions,
    user_with_system_role,
)

from ..models import StaffMember

URL = "/api/v1/staff/"
ALL = ["staff.view", "staff.create", "staff.update", "staff.delete"]


class StaffCRUDTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.authenticate(user_with_permissions(self.org, ALL, email="admin@kmc.test"))

    def payload(self, **overrides):
        data = {
            "employee_number": "E-100",
            "first_name": "Gita",
            "last_name": "Poudel",
            "campus": self.campus.pk,
            "designation": "Head of Department",
            "joined_on": "2020-06-01",
        }
        data.update(overrides)
        return data

    def test_create(self):
        response = self.client.post(URL, self.payload())

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["designation"], "Head of Department")
        self.assertEqual(response.data["staff_type"], "teaching")

    def test_duplicate_employee_number_is_a_400(self):
        self.client.post(URL, self.payload())

        response = self.client.post(URL, self.payload(first_name="Other"))

        self.assertEqual(response.status_code, 400)
        self.assertIn("employee_number", response.data["error"]["details"])

    def test_leaving_needs_a_date(self):
        member = create_staff_member(self.campus)

        without = self.client.patch(f"{URL}{member.pk}/", {"status": "left"})
        with_date = self.client.patch(f"{URL}{member.pk}/", {"status": "left", "left_on": "2026-09-01"})

        self.assertEqual(without.status_code, 400)
        self.assertIn("left_on", without.data["error"]["details"])
        self.assertEqual(with_date.status_code, 200, with_date.data)

    def test_leaving_date_needs_the_left_status(self):
        member = create_staff_member(self.campus)

        response = self.client.patch(f"{URL}{member.pk}/", {"left_on": "2026-09-01"})

        self.assertEqual(response.status_code, 400)

    def test_leaving_before_joining_is_refused(self):
        member = create_staff_member(self.campus, joined_on="2020-06-01")

        response = self.client.patch(f"{URL}{member.pk}/", {"status": "left", "left_on": "2019-01-01"})

        self.assertEqual(response.status_code, 400)

    def test_database_backs_the_leaving_rules(self):
        member = create_staff_member(self.campus)

        with self.assertRaises(IntegrityError), transaction.atomic():
            StaffMember.objects.filter(pk=member.pk).update(status="left")

    def test_filter_by_type(self):
        create_staff_member(self.campus, employee_number="E-1", staff_type="teaching")
        create_staff_member(self.campus, employee_number="E-2", staff_type="non_teaching")

        response = self.client.get(URL, {"staff_type": "non_teaching"})

        self.assertEqual([r["employee_number"] for r in response.data["results"]], ["E-2"])


class StaffAccessTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.lalitpur = create_campus(self.org, code="lalitpur")
        self.bhaktapur = create_campus(self.org, code="bhaktapur")
        create_staff_member(self.lalitpur, employee_number="L-1")
        self.elsewhere = create_staff_member(self.bhaktapur, employee_number="B-1")

    def test_campus_scoped_role_sees_only_its_campus(self):
        self.authenticate(user_with_system_role(self.org, "campus-admin", email="ram@kmc.test", campus=self.lalitpur))

        listed = [r["employee_number"] for r in self.client.get(URL).data["results"]]

        self.assertEqual(listed, ["L-1"])
        self.assertEqual(self.client.get(f"{URL}{self.elsewhere.pk}/").status_code, 404)

    def test_staff_role_can_view_but_not_edit(self):
        self.authenticate(user_with_system_role(self.org, "staff", email="t@kmc.test"))

        self.assertEqual(self.client.get(URL).status_code, 200)
        self.assertEqual(self.client.patch(f"{URL}{self.elsewhere.pk}/", {"phone": "1"}).status_code, 403)

    def test_other_organizations_staff_are_invisible(self):
        self.authenticate(user_with_permissions(create_organization(code="other"), ALL, email="x@other.test"))

        self.assertEqual(self.client.get(URL).data["count"], 0)
        self.assertEqual(self.client.get(f"{URL}{self.elsewhere.pk}/").status_code, 404)

    def test_staff_member_reads_their_own_record(self):
        account = create_user(organization=self.org, email="gita@kmc.test", user_type="teacher")
        StaffMember.objects.filter(pk=self.elsewhere.pk).update(user=account)
        self.authenticate(account)

        self.assertEqual(self.client.get(f"{URL}me/").data["employee_number"], "B-1")
        self.assertEqual(self.client.get(URL).status_code, 403)
