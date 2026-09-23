from django.db import IntegrityError, transaction

from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_parent,
    create_student,
    create_user,
    user_with_permissions,
    user_with_system_role,
)

from ..models import Parent, StudentParent
from ..services import link_student

URL = "/api/v1/parents/"
ALL = ["parents.view", "parents.create", "parents.update", "parents.delete", "students.view"]


class ParentCRUDTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.authenticate(user_with_permissions(self.org, ALL, email="admin@kmc.test"))

    def test_create_and_update(self):
        created = self.client.post(URL, {"first_name": "Mohan", "last_name": "Gurung", "phone": "9800000001"})
        updated = self.client.patch(f"{URL}{created.data['id']}/", {"occupation": "Farmer"})

        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(updated.data["occupation"], "Farmer")
        self.assertEqual(Parent.objects.get().organization, self.org)

    def test_filter_parents_of_a_student(self):
        student = create_student(self.campus)
        mother = create_parent(self.org, first_name="Sita")
        create_parent(self.org, first_name="Unrelated")
        link_student(parent=mother, student=student, relationship="mother")

        response = self.client.get(URL, {"student": student.pk})

        self.assertEqual([r["first_name"] for r in response.data["results"]], ["Sita"])


class LinkingTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.student = create_student(self.campus)
        self.father = create_parent(self.org, first_name="Mohan")
        self.mother = create_parent(self.org, first_name="Sita")
        self.authenticate(user_with_permissions(self.org, ALL, email="admin@kmc.test"))

    def link(self, parent, **data):
        data.setdefault("student", self.student.pk)
        data.setdefault("relationship", "father")
        return self.client.post(f"{URL}{parent.pk}/link-student/", data)

    def test_link_and_list_children(self):
        response = self.link(self.father, is_primary_contact=True)
        children = self.client.get(f"{URL}{self.father.pk}/students/")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(children.data[0]["student"], self.student.pk)
        self.assertTrue(children.data[0]["is_primary_contact"])

    def test_linking_twice_is_a_conflict(self):
        self.link(self.father)

        self.assertEqual(self.link(self.father).status_code, 409)

    def test_new_primary_contact_replaces_the_old_one(self):
        self.link(self.father, is_primary_contact=True)
        self.link(self.mother, relationship="mother", is_primary_contact=True)

        primary = StudentParent.objects.filter(student=self.student, is_primary_contact=True)
        self.assertEqual([link.parent for link in primary], [self.mother])

    def test_database_refuses_two_primary_contacts(self):
        StudentParent.objects.create(
            organization=self.org, student=self.student, parent=self.father,
            relationship="father", is_primary_contact=True,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            StudentParent.objects.create(
                organization=self.org, student=self.student, parent=self.mother,
                relationship="mother", is_primary_contact=True,
            )

    def test_unlink(self):
        self.link(self.father)

        response = self.client.post(f"{URL}{self.father.pk}/unlink-student/", {"student": self.student.pk})

        self.assertEqual(response.status_code, 204)
        self.assertFalse(StudentParent.objects.exists())

    def test_unlinking_a_student_who_is_not_linked_is_an_error(self):
        response = self.client.post(f"{URL}{self.father.pk}/unlink-student/", {"student": self.student.pk})

        self.assertEqual(response.status_code, 400)

    def test_another_organizations_student_is_unknown(self):
        foreign = create_student(create_campus(create_organization(code="other"), code="main"))

        response = self.link(self.father, student=foreign.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn("student", response.data["error"]["details"])

    def test_campus_scoped_user_cannot_link_a_student_outside_their_campus(self):
        other_campus = create_campus(self.org, code="bhaktapur")
        elsewhere = create_student(other_campus, student_number="B-1")
        self.authenticate(user_with_system_role(self.org, "campus-admin", email="ram@kmc.test", campus=self.campus))

        response = self.link(self.father, student=elsewhere.pk)

        self.assertEqual(response.status_code, 400)

    def test_linking_needs_update_permission(self):
        self.authenticate(user_with_permissions(self.org, ["parents.view", "students.view"], email="v@kmc.test"))

        self.assertEqual(self.link(self.father).status_code, 403)


class ParentIsolationTests(APITestCaseBase):
    def test_other_organizations_parents_are_invisible(self):
        org = create_organization(code="kmc")
        foreign = create_parent(create_organization(code="other"), first_name="Foreign")
        self.authenticate(user_with_permissions(org, ALL, email="a@kmc.test"))

        self.assertEqual(self.client.get(URL).data["count"], 0)
        self.assertEqual(self.client.get(f"{URL}{foreign.pk}/").status_code, 404)


class ParentSelfServiceTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        campus = create_campus(self.org, code="main")
        self.account = user_with_system_role(self.org, "parent", email="mohan@kmc.test", user_type="parent")
        self.parent = create_parent(self.org, first_name="Mohan", user=self.account)
        self.child = create_student(campus, student_number="S-1")
        self.not_mine = create_student(campus, student_number="S-2")
        link_student(parent=self.parent, student=self.child, relationship="father")
        self.authenticate(self.account)

    def test_parent_sees_own_profile_and_only_their_children(self):
        response = self.client.get(f"{URL}me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([c["student_number"] for c in response.data["children"]], ["S-1"])

    def test_parent_cannot_browse_other_students_or_parents(self):
        self.assertEqual(self.client.get("/api/v1/students/").status_code, 403)
        self.assertEqual(self.client.get(f"/api/v1/students/{self.not_mine.pk}/").status_code, 403)
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_soft_deleted_child_disappears_from_the_list(self):
        self.child.delete()

        response = self.client.get(f"{URL}me/")

        self.assertEqual(response.data["children"], [])

    def test_account_without_a_parent_profile_gets_404(self):
        self.authenticate(create_user(organization=self.org, email="nobody@kmc.test"))

        self.assertEqual(self.client.get(f"{URL}me/").status_code, 404)
