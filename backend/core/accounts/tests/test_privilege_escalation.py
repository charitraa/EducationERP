"""Holding a management permission must never be a path to more power.

The scenario throughout: a college whose principal is org-admin, and a branch
head (campus-admin) who can manage users and roles but must not be able to
become — or take over — the principal.
"""
from core.permissions.models import Role, UserRole
from tests.base import APITestCaseBase
from tests.factories import (
    DEFAULT_PASSWORD,
    create_campus,
    create_organization,
    create_user,
    user_with_system_role,
)

from ..models import User

USERS_URL = "/api/v1/users/"


class EscalationTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.lalitpur = create_campus(self.org, code="lalitpur")
        self.bhaktapur = create_campus(self.org, code="bhaktapur")

        self.principal = user_with_system_role(self.org, "org-admin", email="principal@kmc.test")
        self.head = user_with_system_role(self.org, "campus-admin", email="head@kmc.test")
        self.student = user_with_system_role(
            self.org, "student", email="student@kmc.test", user_type="student"
        )

        self.org_admin = Role.objects.get(code="org-admin", organization=None)
        self.staff_role = Role.objects.get(code="staff", organization=None)

    def assign(self, user, role, campus=None):
        payload = {"role": role.pk}
        if campus is not None:
            payload["campus"] = campus.pk
        return self.client.post(f"{USERS_URL}{user.pk}/assign-role/", payload)


class RoleGrantTests(EscalationTestCase):
    def test_campus_admin_cannot_promote_themselves_to_org_admin(self):
        self.authenticate(self.head)

        response = self.assign(self.head, self.org_admin)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "role_exceeds_own_permissions")
        self.assertFalse(UserRole.objects.filter(user=self.head, role=self.org_admin).exists())

    def test_campus_admin_cannot_promote_someone_else_to_org_admin(self):
        self.authenticate(self.head)

        response = self.assign(self.student, self.org_admin)

        self.assertEqual(response.status_code, 403)

    def test_campus_admin_can_grant_a_role_within_their_own_permissions(self):
        self.authenticate(self.head)

        response = self.assign(self.student, self.staff_role)

        self.assertEqual(response.status_code, 201, response.data)

    def test_org_admin_can_grant_org_admin(self):
        self.authenticate(self.principal)

        response = self.assign(self.head, self.org_admin)

        self.assertEqual(response.status_code, 201, response.data)

    def test_campus_scoped_holder_cannot_grant_organization_wide(self):
        branch_head = user_with_system_role(
            self.org, "campus-admin", email="branch@kmc.test", campus=self.lalitpur
        )
        self.authenticate(branch_head)

        response = self.assign(self.student, self.staff_role)

        self.assertEqual(response.status_code, 403)

    def test_campus_scoped_holder_can_grant_at_their_own_campus_only(self):
        branch_head = user_with_system_role(
            self.org, "campus-admin", email="branch@kmc.test", campus=self.lalitpur
        )
        self.authenticate(branch_head)

        own = self.assign(self.student, self.staff_role, campus=self.lalitpur)
        other = self.assign(self.student, self.staff_role, campus=self.bhaktapur)

        self.assertEqual(own.status_code, 201, own.data)
        self.assertEqual(other.status_code, 403)

    def test_creating_a_user_with_a_role_above_your_own_is_refused_entirely(self):
        self.authenticate(self.head)

        response = self.client.post(
            USERS_URL,
            {"email": "sneaky@kmc.test", "password": "Sneaky-pass-123", "role_codes": ["org-admin"]},
        )

        self.assertEqual(response.status_code, 403)
        # All or nothing: no half-created account without its role.
        self.assertFalse(User.all_objects.filter(email="sneaky@kmc.test").exists())


class ManagingMorePowerfulUsersTests(EscalationTestCase):
    def setUp(self):
        super().setUp()
        self.authenticate(self.head)

    def test_cannot_reset_the_org_admins_password(self):
        response = self.client.post(
            f"{USERS_URL}{self.principal.pk}/set-password/",
            {"new_password": "Taken-over-12345"},
        )

        self.assertEqual(response.status_code, 403)
        self.principal.refresh_from_db()
        self.assertTrue(self.principal.check_password(DEFAULT_PASSWORD))

    def test_cannot_revoke_the_org_admins_role(self):
        response = self.client.post(
            f"{USERS_URL}{self.principal.pk}/revoke-role/", {"role": self.org_admin.pk}
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(UserRole.objects.filter(user=self.principal, role=self.org_admin).exists())

    def test_cannot_deactivate_edit_or_delete_the_org_admin(self):
        url = f"{USERS_URL}{self.principal.pk}/"

        self.assertEqual(self.client.post(f"{url}deactivate/").status_code, 403)
        self.assertEqual(self.client.patch(url, {"first_name": "Hacked"}).status_code, 403)
        self.assertEqual(self.client.delete(url).status_code, 403)

        self.principal.refresh_from_db()
        self.assertTrue(self.principal.is_active)
        self.assertNotEqual(self.principal.first_name, "Hacked")

    def test_can_still_manage_a_less_powerful_user(self):
        response = self.client.post(
            f"{USERS_URL}{self.student.pk}/set-password/", {"new_password": "New-student-123"}
        )

        self.assertEqual(response.status_code, 204)

    def test_can_manage_their_own_account(self):
        response = self.client.patch(f"{USERS_URL}{self.head.pk}/", {"first_name": "Ram"})

        self.assertEqual(response.status_code, 200)

    def test_nobody_but_a_superuser_manages_a_superuser(self):
        tenant_root = create_user(
            organization=self.org, email="root@kmc.test", is_superuser=True, is_staff=True
        )
        self.authenticate(self.principal)

        response = self.client.patch(f"{USERS_URL}{tenant_root.pk}/", {"first_name": "X"})

        self.assertEqual(response.status_code, 403)
