"""Cross-tenant security tests.

The whole platform is one database shared by many institutions, so these are
the tests that matter most: a valid token from one organization must never
reach another organization's data.
"""
from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_role,
    create_superuser,
    create_user,
    grant,
    user_with_permissions,
)


class CampusIsolationTests(APITestCaseBase):
    def setUp(self):
        self.org_a = create_organization(code="college-a")
        self.org_b = create_organization(code="college-b")

        self.campus_a = create_campus(self.org_a, code="a-main", name="A Main")
        self.campus_b = create_campus(self.org_b, code="b-main", name="B Main")

        self.user_a = user_with_permissions(
            self.org_a,
            ["campuses.view", "campuses.create", "campuses.update", "campuses.delete"],
            email="a@test.edu",
        )
        self.authenticate(self.user_a)

    def test_list_returns_only_own_campuses(self):
        response = self.client.get("/api/v1/campuses/")

        names = {row["name"] for row in response.data["results"]}
        self.assertEqual(names, {"A Main"})

    def test_retrieving_another_orgs_campus_returns_404(self):
        """404, not 403 — existence of other tenants' rows is not disclosed."""
        response = self.client.get(f"/api/v1/campuses/{self.campus_b.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_updating_another_orgs_campus_returns_404(self):
        response = self.client.patch(
            f"/api/v1/campuses/{self.campus_b.pk}/", {"name": "Hijacked"}
        )

        self.assertEqual(response.status_code, 404)
        self.campus_b.refresh_from_db()
        self.assertEqual(self.campus_b.name, "B Main")

    def test_deleting_another_orgs_campus_returns_404(self):
        response = self.client.delete(f"/api/v1/campuses/{self.campus_b.pk}/")

        self.assertEqual(response.status_code, 404)
        self.campus_b.refresh_from_db()
        self.assertIsNone(self.campus_b.deleted_at)

    def test_organization_in_payload_is_ignored_on_create(self):
        """The tenant comes from the token, never from client input."""
        response = self.client.post(
            "/api/v1/campuses/",
            {"name": "Sneaky", "code": "sneaky", "organization": self.org_b.pk},
        )

        self.assertEqual(response.status_code, 201, response.data)
        from core.organizations.models import Campus

        self.assertEqual(Campus.objects.get(code="sneaky").organization_id, self.org_a.pk)


class OrganizationIsolationTests(APITestCaseBase):
    def setUp(self):
        self.org_a = create_organization(code="org-a")
        self.org_b = create_organization(code="org-b")
        self.user_a = user_with_permissions(
            self.org_a, ["organizations.view", "organizations.update"], email="oa@test.edu"
        )
        self.authenticate(self.user_a)

    def test_user_sees_only_their_own_organization(self):
        response = self.client.get("/api/v1/organizations/")

        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["code"], "org-a")

    def test_another_organization_is_not_retrievable(self):
        response = self.client.get(f"/api/v1/organizations/{self.org_b.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_platform_admin_sees_every_organization(self):
        self.logout()
        self.authenticate(create_superuser())

        response = self.client.get("/api/v1/organizations/")
        codes = {row["code"] for row in response.data["results"]}
        self.assertEqual(codes, {"org-a", "org-b"})


class UserIsolationTests(APITestCaseBase):
    def setUp(self):
        self.org_a = create_organization(code="users-a")
        self.org_b = create_organization(code="users-b")

        self.admin_a = user_with_permissions(
            self.org_a,
            ["users.view", "users.create", "users.update", "users.delete", "users.manage_roles"],
            email="admin-a@test.edu",
        )
        self.victim_b = create_user(self.org_b, email="victim-b@test.edu")
        self.authenticate(self.admin_a)

    def test_list_excludes_users_of_other_organizations(self):
        response = self.client.get("/api/v1/users/")

        emails = {row["email"] for row in response.data["results"]}
        self.assertNotIn("victim-b@test.edu", emails)

    def test_cannot_read_a_user_from_another_organization(self):
        response = self.client.get(f"/api/v1/users/{self.victim_b.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_cannot_reset_the_password_of_a_user_in_another_organization(self):
        response = self.client.post(
            f"/api/v1/users/{self.victim_b.pk}/set-password/",
            {"new_password": "Attacker-owns-you-1"},
        )

        self.assertEqual(response.status_code, 404)
        self.victim_b.refresh_from_db()
        self.assertFalse(self.victim_b.check_password("Attacker-owns-you-1"))

    def test_cannot_assign_a_role_from_another_organization(self):
        target = create_user(self.org_a, email="target-a@test.edu")
        foreign_role = create_role(self.org_b, code="foreign-role")

        response = self.client.post(
            f"/api/v1/users/{target.pk}/assign-role/", {"role": foreign_role.pk}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(target.role_assignments.count(), 0)

    def test_new_users_are_created_inside_the_callers_organization(self):
        response = self.client.post(
            "/api/v1/users/",
            {
                "email": "newbie@test.edu",
                "password": "Fresh-pass-12345",
                "first_name": "New",
                "user_type": "staff",
            },
        )

        self.assertEqual(response.status_code, 201, response.data)
        from core.accounts.models import User

        self.assertEqual(User.objects.get(email="newbie@test.edu").organization_id, self.org_a.pk)


class AuditIsolationTests(APITestCaseBase):
    def setUp(self):
        self.org_a = create_organization(code="audit-a")
        self.org_b = create_organization(code="audit-b")

        self.auditor_a = user_with_permissions(
            self.org_a, ["audit.view"], email="auditor-a@test.edu"
        )
        self.admin_b = user_with_permissions(
            self.org_b, ["campuses.create"], email="admin-b@test.edu"
        )

    def test_audit_entries_of_other_organizations_are_invisible(self):
        # org B performs an audited action.
        self.authenticate(self.admin_b)
        self.client.post("/api/v1/campuses/", {"name": "B Campus", "code": "b-campus"})
        self.logout()

        self.authenticate(self.auditor_a)
        response = self.client.get("/api/v1/audit-logs/")

        self.assertEqual(response.status_code, 200)
        for row in response.data["results"]:
            self.assertNotEqual(row["object_repr"], "B Campus")


class InactiveAndUnscopedUserTests(APITestCaseBase):
    def test_user_without_an_organization_sees_nothing(self):
        """A non-superuser with no tenant must not fall through to all rows."""
        org = create_organization(code="orphan-college")
        create_campus(org, code="orphan-campus")

        orphan = create_user(None, email="orphan@test.edu")
        role = create_role(None, code="orphan-role", permissions=["campuses.view"])
        role.is_system = True
        role.save()
        grant(orphan, role)

        self.authenticate(orphan)
        response = self.client.get("/api/v1/campuses/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
