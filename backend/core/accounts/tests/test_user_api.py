from core.audit.models import AuditLog
from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_role,
    create_user,
    user_with_permissions,
)

from ..models import User

USERS_URL = "/api/v1/users/"

ALL_USER_PERMISSIONS = [
    "users.view", "users.create", "users.update", "users.delete", "users.manage_roles",
]


class UserCRUDTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="crud-college")
        self.admin = user_with_permissions(
            self.org, ALL_USER_PERMISSIONS, email="admin@test.edu"
        )
        self.authenticate(self.admin)

    def test_create_user(self):
        response = self.client.post(
            USERS_URL,
            {
                "email": "Student.One@Test.edu",
                "password": "Student-pass-123",
                "first_name": "Student",
                "last_name": "One",
                "user_type": "student",
            },
        )

        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(email="student.one@test.edu")
        self.assertEqual(user.user_type, "student")
        self.assertTrue(user.check_password("Student-pass-123"))

    def test_password_is_never_returned(self):
        response = self.client.post(
            USERS_URL, {"email": "secret@test.edu", "password": "Secret-pass-123"}
        )
        self.assertNotIn("password", response.data)

    def test_duplicate_email_is_rejected(self):
        create_user(self.org, email="taken@test.edu")
        response = self.client.post(
            USERS_URL, {"email": "taken@test.edu", "password": "Some-pass-1234"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data["error"]["details"])

    def test_weak_password_is_rejected(self):
        response = self.client.post(
            USERS_URL, {"email": "weak@test.edu", "password": "1234"}
        )
        self.assertEqual(response.status_code, 400)

    def test_create_with_roles_assigns_them(self):
        response = self.client.post(
            USERS_URL,
            {
                "email": "withrole@test.edu",
                "password": "With-role-12345",
                # A role carrying no permissions: granting one needs no more
                # than the caller holds (see test_privilege_escalation).
                "role_codes": ["student"],
            },
        )

        self.assertEqual(response.status_code, 201, response.data)
        user = User.objects.get(email="withrole@test.edu")
        self.assertEqual(
            list(user.role_assignments.values_list("role__code", flat=True)), ["student"]
        )

    def test_create_with_unknown_role_is_rejected(self):
        response = self.client.post(
            USERS_URL,
            {
                "email": "badrole@test.edu",
                "password": "Bad-role-12345",
                "role_codes": ["does-not-exist"],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email="badrole@test.edu").exists())

    def test_retrieve_user(self):
        user = create_user(self.org, email="target@test.edu")
        response = self.client.get(f"{USERS_URL}{user.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "target@test.edu")

    def test_update_user(self):
        user = create_user(self.org, email="update@test.edu")
        response = self.client.patch(f"{USERS_URL}{user.pk}/", {"first_name": "Renamed"})

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Renamed")

    def test_delete_is_a_soft_delete(self):
        user = create_user(self.org, email="soft@test.edu")
        response = self.client.delete(f"{USERS_URL}{user.pk}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.filter(pk=user.pk).exists())
        deleted = User.all_objects.get(pk=user.pk)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(deleted.deleted_by_id, self.admin.pk)

    def test_deactivate_keeps_the_record_but_blocks_login(self):
        user = create_user(self.org, email="deact@test.edu")
        response = self.client.post(f"{USERS_URL}{user.pk}/deactivate/")

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_soft_deleted_user_cannot_authenticate(self):
        user = create_user(self.org, email="ghost@test.edu")
        user.delete()

        from tests.factories import DEFAULT_PASSWORD

        self.logout()
        response = self.client.post(
            "/api/v1/auth/login/", {"email": user.email, "password": DEFAULT_PASSWORD}
        )
        self.assertEqual(response.status_code, 400)

    def test_list_is_paginated(self):
        for i in range(5):
            create_user(self.org, email=f"bulk{i}@test.edu")

        response = self.client.get(USERS_URL)
        self.assertIn("count", response.data)
        self.assertIn("results", response.data)

    def test_search_by_email(self):
        create_user(self.org, email="findme@test.edu")
        response = self.client.get(USERS_URL, {"search": "findme"})

        emails = [row["email"] for row in response.data["results"]]
        self.assertEqual(emails, ["findme@test.edu"])

    def test_filter_by_user_type(self):
        create_user(self.org, email="astudent@test.edu", user_type="student")
        response = self.client.get(USERS_URL, {"user_type": "student"})

        self.assertTrue(
            all(row["user_type"] == "student" for row in response.data["results"])
        )


class UserPermissionEnforcementTests(APITestCaseBase):
    """Every action is gated by its own permission code."""

    def setUp(self):
        self.org = create_organization(code="perm-college")
        self.target = create_user(self.org, email="target@test.edu")

    def _as(self, permissions, email):
        user = user_with_permissions(self.org, permissions, email=email)
        self.logout()
        self.authenticate(user)
        return user

    def test_anonymous_requests_are_rejected(self):
        self.assertEqual(self.client.get(USERS_URL).status_code, 401)

    def test_user_without_permissions_cannot_list(self):
        self._as([], "nobody@test.edu")
        self.assertEqual(self.client.get(USERS_URL).status_code, 403)

    def test_view_permission_does_not_grant_create(self):
        self._as(["users.view"], "viewer@test.edu")

        self.assertEqual(self.client.get(USERS_URL).status_code, 200)
        response = self.client.post(
            USERS_URL, {"email": "x@test.edu", "password": "Some-pass-12345"}
        )
        self.assertEqual(response.status_code, 403)

    def test_update_permission_does_not_grant_delete(self):
        self._as(["users.view", "users.update"], "updater@test.edu")

        self.assertEqual(
            self.client.patch(f"{USERS_URL}{self.target.pk}/", {"first_name": "Ok"}).status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(f"{USERS_URL}{self.target.pk}/").status_code, 403
        )

    def test_managing_roles_needs_its_own_permission(self):
        self._as(["users.view", "users.update"], "norole@test.edu")
        role = create_role(self.org, code="some-role")

        response = self.client.post(
            f"{USERS_URL}{self.target.pk}/assign-role/", {"role": role.pk}
        )
        self.assertEqual(response.status_code, 403)

    def test_revoked_permission_takes_effect_on_the_next_request(self):
        user = self._as(["users.view"], "revokee@test.edu")
        self.assertEqual(self.client.get(USERS_URL).status_code, 200)

        user.role_assignments.all().delete()
        self.assertEqual(self.client.get(USERS_URL).status_code, 403)


class RoleAssignmentAPITests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="assign-college")
        self.campus = create_campus(self.org, code="main")
        self.admin = user_with_permissions(
            self.org, ALL_USER_PERMISSIONS, email="assigner@test.edu"
        )
        self.target = create_user(self.org, email="assignee@test.edu")
        self.role = create_role(self.org, code="teacher", permissions=["users.view"])
        self.authenticate(self.admin)

    def test_assign_role(self):
        response = self.client.post(
            f"{USERS_URL}{self.target.pk}/assign-role/", {"role": self.role.pk}
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(self.target.role_assignments.filter(role=self.role).exists())

    def test_assign_role_scoped_to_a_campus(self):
        response = self.client.post(
            f"{USERS_URL}{self.target.pk}/assign-role/",
            {"role": self.role.pk, "campus": self.campus.pk},
        )

        self.assertEqual(response.status_code, 201, response.data)
        assignment = self.target.role_assignments.get()
        self.assertEqual(assignment.campus_id, self.campus.pk)

    def test_assigning_the_same_role_twice_conflicts(self):
        self.client.post(f"{USERS_URL}{self.target.pk}/assign-role/", {"role": self.role.pk})
        response = self.client.post(
            f"{USERS_URL}{self.target.pk}/assign-role/", {"role": self.role.pk}
        )

        self.assertEqual(response.status_code, 409)

    def test_revoke_role(self):
        self.client.post(f"{USERS_URL}{self.target.pk}/assign-role/", {"role": self.role.pk})
        response = self.client.post(
            f"{USERS_URL}{self.target.pk}/revoke-role/", {"role": self.role.pk}
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(self.target.role_assignments.exists())

    def test_revoking_a_role_that_is_not_assigned_returns_404(self):
        response = self.client.post(
            f"{USERS_URL}{self.target.pk}/revoke-role/", {"role": self.role.pk}
        )
        self.assertEqual(response.status_code, 404)

    def test_listing_a_users_roles(self):
        self.client.post(f"{USERS_URL}{self.target.pk}/assign-role/", {"role": self.role.pk})
        response = self.client.get(f"{USERS_URL}{self.target.pk}/roles/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["role_code"], "teacher")

    def test_role_changes_are_audited(self):
        self.client.post(f"{USERS_URL}{self.target.pk}/assign-role/", {"role": self.role.pk})

        entry = AuditLog.objects.filter(action=AuditLog.Action.PERMISSION_CHANGE).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.metadata["granted_role"], "teacher")
