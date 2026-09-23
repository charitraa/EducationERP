from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from tests.factories import create_campus, create_organization, create_student

from ..models import Enrollment, Student
from ..services import change_student_status, transfer_student


class StudentConstraintTests(TestCase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")

    def test_student_number_is_unique_within_an_organization(self):
        create_student(self.campus, student_number="S-1")

        with self.assertRaises(ValidationError):
            create_student(self.campus, student_number="S-1")

    def test_same_number_is_allowed_in_another_organization(self):
        other_campus = create_campus(create_organization(code="sunrise"), code="main")
        create_student(self.campus, student_number="S-1")

        create_student(other_campus, student_number="S-1")  # no error

    def test_number_is_freed_by_soft_delete(self):
        create_student(self.campus, student_number="S-1").delete()

        create_student(self.campus, student_number="S-1")  # no error

    def test_campus_from_another_organization_is_refused(self):
        foreign = create_campus(create_organization(code="sunrise"), code="main")
        from core.common.exceptions import ServiceError
        from ..services import create_student as service_create

        with self.assertRaises(ServiceError):
            service_create(
                organization_id=self.org.pk, campus=foreign, student_number="S-1",
                first_name="A", last_name="B",
            )


class EnrollmentInvariantTests(TestCase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.student = create_student(self.campus, admitted_on=date(2026, 4, 1))

    def test_creating_a_student_opens_one_enrollment(self):
        enrollment = Enrollment.objects.get(student=self.student)

        self.assertEqual(enrollment.status, Enrollment.Status.ACTIVE)
        self.assertEqual(enrollment.campus, self.campus)
        self.assertEqual(enrollment.started_on, date(2026, 4, 1))
        self.assertIsNone(enrollment.ended_on)

    def test_database_refuses_a_second_open_enrollment(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Enrollment.objects.create(
                organization=self.org, student=self.student, campus=self.campus,
                started_on=date(2026, 5, 1),
            )

    def test_database_refuses_a_closed_enrollment_without_an_end_date(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Enrollment.objects.filter(student=self.student).update(status="completed")

    def test_database_refuses_an_end_before_the_start(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Enrollment.objects.filter(student=self.student).update(
                status="completed", ended_on=date(2026, 3, 1)
            )

    def test_transfer_closes_one_enrollment_and_opens_another(self):
        other = create_campus(self.org, code="lalitpur")

        transfer_student(student=self.student, to_campus=other, on_date=date(2026, 8, 1))

        self.student.refresh_from_db()
        self.assertEqual(self.student.campus, other)
        history = list(self.student.enrollments.order_by("started_on").values_list("campus__code", "status", "ended_on"))
        self.assertEqual(
            history,
            [("main", "transferred", date(2026, 8, 1)), ("lalitpur", "active", None)],
        )

    def test_graduating_closes_the_enrollment_as_completed(self):
        change_student_status(student=self.student, status="graduated", on_date=date(2027, 3, 31))

        self.student.refresh_from_db()
        self.assertEqual(self.student.status, Student.Status.GRADUATED)
        enrollment = self.student.enrollments.get()
        self.assertEqual((enrollment.status, enrollment.ended_on), ("completed", date(2027, 3, 31)))

    def test_suspending_keeps_the_enrollment_open(self):
        change_student_status(student=self.student, status="suspended")

        self.assertEqual(self.student.enrollments.get().status, Enrollment.Status.ACTIVE)
