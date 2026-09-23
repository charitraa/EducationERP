from modules.parents.models import Parent, StudentParent
from modules.students.models import Enrollment, Student
from tests.base import APITestCaseBase
from tests.factories import (
    create_admission,
    create_campus,
    create_organization,
    create_student,
    user_with_permissions,
    user_with_system_role,
)

from ..models import Admission

URL = "/api/v1/admissions/"
ALL = [
    "admissions.view", "admissions.create", "admissions.update", "admissions.delete",
    "admissions.review", "admissions.enroll", "students.create",
]


class AdmissionTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.authenticate(user_with_permissions(self.org, ALL, email="office@kmc.test"))

    def act(self, admission, action, **data):
        return self.client.post(f"{URL}{admission.pk}/{action}/", data)


class AdmissionRecordTests(AdmissionTestCase):
    def test_record_an_application(self):
        response = self.client.post(
            URL,
            {
                "application_number": "A-2026-001",
                "campus": self.campus.pk,
                "first_name": "Anisha",
                "last_name": "Magar",
                "applying_for": "Grade 11 Science",
                "guardian_first_name": "Hari",
                "guardian_relationship": "father",
            },
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "pending")

    def test_status_cannot_be_set_directly(self):
        admission = create_admission(self.campus)

        self.client.patch(f"{URL}{admission.pk}/", {"status": "approved"})

        admission.refresh_from_db()
        self.assertEqual(admission.status, "pending")

    def test_duplicate_application_number_is_a_400(self):
        create_admission(self.campus, application_number="A-1")

        response = self.client.post(
            URL, {"application_number": "A-1", "campus": self.campus.pk, "first_name": "X", "last_name": "Y"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("application_number", response.data["error"]["details"])

    def test_only_pending_applications_can_be_edited(self):
        admission = create_admission(self.campus)
        self.act(admission, "approve")

        response = self.client.patch(f"{URL}{admission.pk}/", {"first_name": "Changed"})

        self.assertEqual(response.status_code, 400)

    def test_approved_or_enrolled_applications_cannot_be_deleted(self):
        admission = create_admission(self.campus)
        self.act(admission, "approve")

        response = self.client.delete(f"{URL}{admission.pk}/")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Admission.objects.filter(pk=admission.pk).exists())

    def test_a_rejected_application_can_be_deleted(self):
        admission = create_admission(self.campus)
        self.act(admission, "reject", note="Incomplete documents")

        self.assertEqual(self.client.delete(f"{URL}{admission.pk}/").status_code, 204)


class DecisionTests(AdmissionTestCase):
    def setUp(self):
        super().setUp()
        self.admission = create_admission(self.campus)

    def test_approve_records_who_and_when(self):
        response = self.act(self.admission, "approve", note="Meets criteria")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "approved")
        self.assertIsNotNone(response.data["decided_at"])
        self.assertEqual(response.data["decision_note"], "Meets criteria")

    def test_reject_needs_a_reason(self):
        without = self.act(self.admission, "reject")
        with_reason = self.act(self.admission, "reject", note="Age requirement not met")

        self.assertEqual(without.status_code, 400)
        self.assertEqual(with_reason.status_code, 200)

    def test_decisions_are_final(self):
        self.act(self.admission, "reject", note="No seats")

        response = self.act(self.admission, "approve")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "invalid_transition")

    def test_an_approved_application_can_be_withdrawn(self):
        self.act(self.admission, "approve")

        response = self.act(self.admission, "withdraw", note="Chose another school")

        self.assertEqual(response.data["status"], "withdrawn")

    def test_reviewing_needs_the_review_permission(self):
        self.authenticate(
            user_with_permissions(self.org, ["admissions.view", "admissions.update"], email="clerk@kmc.test")
        )

        self.assertEqual(self.act(self.admission, "approve").status_code, 403)


class EnrollTests(AdmissionTestCase):
    def setUp(self):
        super().setUp()
        self.admission = create_admission(
            self.campus,
            first_name="Anisha",
            last_name="Magar",
            guardian_first_name="Hari",
            guardian_last_name="Magar",
            guardian_relationship="father",
            guardian_phone="9800000002",
        )
        self.act(self.admission, "approve")

    def test_enroll_creates_the_student_enrollment_and_guardian(self):
        response = self.act(self.admission, "enroll", student_number="S-500", started_on="2026-04-15")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "enrolled")

        student = Student.objects.get(pk=response.data["student"])
        self.assertEqual((student.full_name, student.campus), ("Anisha Magar", self.campus))
        enrollment = Enrollment.objects.get(student=student)
        self.assertEqual(str(enrollment.started_on), "2026-04-15")

        link = StudentParent.objects.get(student=student)
        self.assertEqual((link.parent.full_name, link.relationship), ("Hari Magar", "father"))
        self.assertTrue(link.is_primary_contact)

    def test_without_guardian_details_no_parent_is_created(self):
        bare = create_admission(self.campus, application_number="A-2")
        self.act(bare, "approve")

        self.act(bare, "enroll", student_number="S-501")

        self.assertEqual(Parent.objects.count(), 0)

    def test_enrolling_twice_is_refused(self):
        self.act(self.admission, "enroll", student_number="S-500")

        response = self.act(self.admission, "enroll", student_number="S-999")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Student.objects.count(), 1)

    def test_a_pending_application_cannot_be_enrolled(self):
        pending = create_admission(self.campus, application_number="A-2")

        self.assertEqual(self.act(pending, "enroll", student_number="S-1").status_code, 409)

    def test_taken_student_number_leaves_nothing_behind(self):
        create_student(self.campus, student_number="S-500")

        response = self.act(self.admission, "enroll", student_number="S-500")

        self.assertEqual(response.status_code, 400)
        self.admission.refresh_from_db()
        self.assertEqual(self.admission.status, "approved")
        self.assertEqual(Parent.objects.count(), 0)
        self.assertEqual(Student.objects.count(), 1)

    def test_enrolling_needs_the_right_to_create_students(self):
        self.authenticate(
            user_with_permissions(
                self.org, ["admissions.view", "admissions.enroll"], email="noc@kmc.test"
            )
        )

        response = self.act(self.admission, "enroll", student_number="S-500")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Student.objects.exists())


class AdmissionScopeTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.lalitpur = create_campus(self.org, code="lalitpur")
        self.bhaktapur = create_campus(self.org, code="bhaktapur")
        create_admission(self.lalitpur, application_number="L-1")
        self.elsewhere = create_admission(self.bhaktapur, application_number="B-1")

    def test_campus_admin_works_only_on_their_campus(self):
        self.authenticate(user_with_system_role(self.org, "campus-admin", email="ram@kmc.test", campus=self.lalitpur))

        listed = [r["application_number"] for r in self.client.get(URL).data["results"]]

        self.assertEqual(listed, ["L-1"])
        self.assertEqual(self.client.post(f"{URL}{self.elsewhere.pk}/approve/").status_code, 404)

    def test_other_organizations_applications_are_invisible(self):
        self.authenticate(user_with_permissions(create_organization(code="other"), ALL, email="x@other.test"))

        self.assertEqual(self.client.get(URL).data["count"], 0)
        self.assertEqual(self.client.get(f"{URL}{self.elsewhere.pk}/").status_code, 404)
