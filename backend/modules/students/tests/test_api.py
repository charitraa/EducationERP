from core.audit.models import AuditLog
from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_student,
    create_user,
    user_with_permissions,
    user_with_system_role,
)

from ..models import Student

URL = "/api/v1/students/"
ALL = [
    "students.view", "students.create", "students.update", "students.delete",
    "students.change_status",
]


class StudentCRUDTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main", name="Main")
        self.admin = user_with_permissions(self.org, ALL, email="admin@kmc.test")
        self.authenticate(self.admin)

    def payload(self, **overrides):
        data = {
            "student_number": "S-100",
            "first_name": "Aarav",
            "last_name": "Gurung",
            "campus": self.campus.pk,
            "admitted_on": "2026-04-01",
        }
        data.update(overrides)
        return data

    def test_create_opens_an_enrollment_and_returns_it(self):
        response = self.client.post(URL, self.payload())

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "active")
        self.assertEqual(response.data["current_enrollment"]["campus"], self.campus.pk)
        self.assertEqual(response.data["current_enrollment"]["started_on"], "2026-04-01")

    def test_create_is_audited_once(self):
        self.client.post(URL, self.payload())

        entries = AuditLog.objects.filter(module="students", action=AuditLog.Action.CREATE)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.get().actor, self.admin)

    def test_duplicate_student_number_is_a_400_naming_the_field(self):
        self.client.post(URL, self.payload())

        response = self.client.post(URL, self.payload(first_name="Other"))

        self.assertEqual(response.status_code, 400)
        self.assertIn("student_number", response.data["error"]["details"])

    def test_status_and_organization_in_the_payload_are_ignored(self):
        other_org = create_organization(code="other")

        response = self.client.post(
            URL, self.payload(status="graduated", organization=other_org.pk)
        )

        self.assertEqual(response.status_code, 201, response.data)
        student = Student.objects.get(pk=response.data["id"])
        self.assertEqual((student.status, student.organization), ("active", self.org))

    def test_campus_of_another_organization_is_unknown(self):
        foreign = create_campus(create_organization(code="other"), code="main")

        response = self.client.post(URL, self.payload(campus=foreign.pk))

        self.assertEqual(response.status_code, 400)
        self.assertIn("campus", response.data["error"]["details"])

    def test_update_details(self):
        student = create_student(self.campus)

        response = self.client.patch(f"{URL}{student.pk}/", {"phone": "9800000000"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["phone"], "9800000000")

    def test_campus_cannot_be_changed_by_editing(self):
        student = create_student(self.campus)
        other = create_campus(self.org, code="lalitpur")

        response = self.client.patch(f"{URL}{student.pk}/", {"campus": other.pk})

        self.assertEqual(response.status_code, 400)
        self.assertIn("transfer", str(response.data["error"]["details"]["campus"]))

    def test_admission_date_cannot_be_changed(self):
        student = create_student(self.campus)

        response = self.client.patch(f"{URL}{student.pk}/", {"admitted_on": "2020-01-01"})

        self.assertEqual(response.status_code, 400)

    def test_delete_is_soft(self):
        student = create_student(self.campus)

        response = self.client.delete(f"{URL}{student.pk}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Student.objects.filter(pk=student.pk).exists())
        self.assertTrue(Student.all_objects.filter(pk=student.pk).exists())

    def test_search_and_filter(self):
        create_student(self.campus, student_number="S-1", first_name="Aarav")
        suspended = create_student(self.campus, student_number="S-2", first_name="Kiran")
        Student.objects.filter(pk=suspended.pk).update(status="suspended")

        by_search = self.client.get(URL, {"search": "aarav"})
        by_status = self.client.get(URL, {"status": "suspended"})

        self.assertEqual([r["student_number"] for r in by_search.data["results"]], ["S-1"])
        self.assertEqual([r["student_number"] for r in by_status.data["results"]], ["S-2"])

    def test_linking_a_login_account(self):
        account = create_user(organization=self.org, email="aarav@kmc.test", user_type="student")

        response = self.client.post(URL, self.payload(user=account.pk))

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Student.objects.get(pk=response.data["id"]).user, account)

    def test_an_account_links_to_one_student_only(self):
        account = create_user(organization=self.org, email="aarav@kmc.test")
        create_student(self.campus, student_number="S-1", user=account)

        response = self.client.post(URL, self.payload(user=account.pk))

        self.assertEqual(response.status_code, 400)
        self.assertIn("user", response.data["error"]["details"])

    def test_another_organizations_account_is_unknown(self):
        foreign = create_user(organization=create_organization(code="other"), email="x@other.test")

        response = self.client.post(URL, self.payload(user=foreign.pk))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(str(response.data["error"]["details"]["user"][0]), "Unknown user.")


class TransferAndStatusTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.main = create_campus(self.org, code="main")
        self.lalitpur = create_campus(self.org, code="lalitpur")
        self.student = create_student(self.main, admitted_on="2026-04-01")
        self.authenticate(user_with_permissions(self.org, ALL, email="admin@kmc.test"))

    def test_transfer(self):
        response = self.client.post(
            f"{URL}{self.student.pk}/transfer/",
            {"campus": self.lalitpur.pk, "on_date": "2026-08-01", "reason": "Moved house"},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["campus"], self.lalitpur.pk)
        history = self.client.get(f"{URL}{self.student.pk}/enrollments/").data
        self.assertEqual([e["status"] for e in history], ["active", "transferred"])
        self.assertEqual(history[1]["end_reason"], "Moved house")

    def test_transfer_to_the_same_campus_is_refused(self):
        response = self.client.post(f"{URL}{self.student.pk}/transfer/", {"campus": self.main.pk})

        self.assertEqual(response.status_code, 400)

    def test_transfer_dated_before_the_enrollment_is_refused(self):
        response = self.client.post(
            f"{URL}{self.student.pk}/transfer/",
            {"campus": self.lalitpur.pk, "on_date": "2026-01-01"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.student.enrollments.count(), 1)

    def test_graduate(self):
        response = self.client.post(f"{URL}{self.student.pk}/change-status/", {"status": "graduated"})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "graduated")
        self.assertIsNone(response.data["current_enrollment"])

    def test_graduation_recorded_ahead_keeps_the_student_in_class_until_then(self):
        response = self.client.post(
            f"{URL}{self.student.pk}/change-status/", {"status": "graduated", "on_date": "2099-03-31"}
        )

        self.assertEqual(response.data["status"], "graduated")
        self.assertEqual(response.data["current_enrollment"]["ended_on"], "2099-03-31")

    def test_graduated_is_final(self):
        self.client.post(f"{URL}{self.student.pk}/change-status/", {"status": "graduated"})

        response = self.client.post(f"{URL}{self.student.pk}/change-status/", {"status": "active"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "invalid_transition")

    def test_graduated_student_cannot_be_transferred(self):
        self.client.post(f"{URL}{self.student.pk}/change-status/", {"status": "graduated"})

        response = self.client.post(f"{URL}{self.student.pk}/transfer/", {"campus": self.lalitpur.pk})

        self.assertEqual(response.status_code, 409)

    def test_suspend_then_reactivate(self):
        suspend = self.client.post(f"{URL}{self.student.pk}/change-status/", {"status": "suspended"})
        reactivate = self.client.post(f"{URL}{self.student.pk}/change-status/", {"status": "active"})

        self.assertEqual((suspend.status_code, reactivate.status_code), (200, 200))
        self.assertEqual(reactivate.data["status"], "active")

    def test_status_changes_are_audited(self):
        self.client.post(f"{URL}{self.student.pk}/change-status/", {"status": "suspended", "reason": "Conduct"})

        entry = AuditLog.objects.filter(module="students", action="update").latest("created_at")
        self.assertEqual(entry.changes, {"status": {"before": "active", "after": "suspended"}})
        self.assertEqual(entry.metadata["reason"], "Conduct")


class StudentPermissionTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.student = create_student(self.campus)

    def test_anonymous_is_rejected(self):
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_view_only_cannot_write(self):
        self.authenticate(user_with_permissions(self.org, ["students.view"], email="v@kmc.test"))

        self.assertEqual(self.client.get(URL).status_code, 200)
        self.assertEqual(self.client.patch(f"{URL}{self.student.pk}/", {"phone": "1"}).status_code, 403)
        self.assertEqual(self.client.delete(f"{URL}{self.student.pk}/").status_code, 403)

    def test_update_does_not_allow_status_changes(self):
        self.authenticate(user_with_permissions(self.org, ["students.view", "students.update"], email="u@kmc.test"))

        response = self.client.post(f"{URL}{self.student.pk}/change-status/", {"status": "withdrawn"})

        self.assertEqual(response.status_code, 403)

    def test_staff_role_can_view_but_not_edit(self):
        self.authenticate(user_with_system_role(self.org, "staff", email="t@kmc.test"))

        self.assertEqual(self.client.get(URL).status_code, 200)
        self.assertEqual(self.client.post(URL, {}).status_code, 403)


class StudentIsolationTests(APITestCaseBase):
    """Another organization's students, and other campuses for scoped roles."""

    def setUp(self):
        self.org = create_organization(code="kmc")
        self.lalitpur = create_campus(self.org, code="lalitpur")
        self.bhaktapur = create_campus(self.org, code="bhaktapur")
        self.at_lalitpur = create_student(self.lalitpur, student_number="L-1")
        self.at_bhaktapur = create_student(self.bhaktapur, student_number="B-1")

        other_org = create_organization(code="other")
        self.foreign = create_student(create_campus(other_org, code="main"), student_number="X-1")

    def test_other_organizations_students_are_invisible(self):
        self.authenticate(user_with_permissions(self.org, ALL, email="a@kmc.test"))

        listed = {r["student_number"] for r in self.client.get(URL).data["results"]}

        self.assertEqual(listed, {"L-1", "B-1"})
        self.assertEqual(self.client.get(f"{URL}{self.foreign.pk}/").status_code, 404)
        self.assertEqual(self.client.patch(f"{URL}{self.foreign.pk}/", {"phone": "1"}).status_code, 404)

    def test_campus_scoped_role_sees_only_its_campus(self):
        self.authenticate(user_with_system_role(self.org, "campus-admin", email="ram@kmc.test", campus=self.lalitpur))

        listed = {r["student_number"] for r in self.client.get(URL).data["results"]}

        self.assertEqual(listed, {"L-1"})
        self.assertEqual(self.client.get(f"{URL}{self.at_bhaktapur.pk}/").status_code, 404)

    def test_campus_scoped_role_cannot_create_at_another_campus(self):
        self.authenticate(user_with_system_role(self.org, "campus-admin", email="ram@kmc.test", campus=self.lalitpur))

        response = self.client.post(
            URL,
            {"student_number": "B-2", "first_name": "A", "last_name": "B", "campus": self.bhaktapur.pk},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Student.objects.filter(student_number="B-2").exists())

    def test_campus_scoped_role_cannot_transfer_out_of_its_campus(self):
        self.authenticate(user_with_system_role(self.org, "campus-admin", email="ram@kmc.test", campus=self.lalitpur))

        response = self.client.post(f"{URL}{self.at_lalitpur.pk}/transfer/", {"campus": self.bhaktapur.pk})

        self.assertEqual(response.status_code, 403)
        self.at_lalitpur.refresh_from_db()
        self.assertEqual(self.at_lalitpur.campus, self.lalitpur)


class StudentSelfServiceTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.account = user_with_system_role(self.org, "student", email="aarav@kmc.test", user_type="student")
        self.own = create_student(self.campus, student_number="S-1", user=self.account)
        self.classmate = create_student(self.campus, student_number="S-2")
        self.authenticate(self.account)

    def test_student_reads_their_own_record(self):
        response = self.client.get(f"{URL}me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["student_number"], "S-1")

    def test_student_cannot_read_anyone_elses_record(self):
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.assertEqual(self.client.get(f"{URL}{self.classmate.pk}/").status_code, 403)

    def test_account_without_a_student_record_gets_404(self):
        self.authenticate(create_user(organization=self.org, email="nobody@kmc.test"))

        self.assertEqual(self.client.get(f"{URL}me/").status_code, 404)
