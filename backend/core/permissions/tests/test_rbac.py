from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

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

from ..models import Permission, Role, UserRole
from ..selectors import get_user_permission_codes


class PermissionResolutionTests(APITestCaseBase):
    """User -> Role -> Permission, including campus scoping and expiry."""

    def setUp(self):
        self.org = create_organization(code="rbac-college")
        self.campus_a = create_campus(self.org, code="campus-a")
        self.campus_b = create_campus(self.org, code="campus-b")
        self.user = create_user(self.org, email="rbac@test.edu")

    def test_user_without_roles_has_no_permissions(self):
        self.assertEqual(get_user_permission_codes(self.user), set())

    def test_permissions_come_through_assigned_roles(self):
        role = create_role(self.org, code="viewer", permissions=["users.view"])
        grant(self.user, role)

        self.assertEqual(get_user_permission_codes(self.user), {"users.view"})

    def test_permissions_from_multiple_roles_are_merged(self):
        grant(self.user, create_role(self.org, code="r1", permissions=["users.view"]))
        grant(self.user, create_role(self.org, code="r2", permissions=["roles.view"]))

        self.assertEqual(
            get_user_permission_codes(self.user), {"users.view", "roles.view"}
        )

    def test_campus_scoped_role_only_applies_at_that_campus(self):
        role = create_role(self.org, code="campus-viewer", permissions=["users.view"])
        grant(self.user, role, campus=self.campus_a)

        self.assertEqual(
            get_user_permission_codes(self.user, campus=self.campus_a), {"users.view"}
        )
        self.assertEqual(get_user_permission_codes(self.user, campus=self.campus_b), set())

    def test_organization_wide_role_applies_at_every_campus(self):
        grant(self.user, create_role(self.org, code="org-viewer", permissions=["users.view"]))

        self.assertEqual(
            get_user_permission_codes(self.user, campus=self.campus_a), {"users.view"}
        )
        self.assertEqual(
            get_user_permission_codes(self.user, campus=self.campus_b), {"users.view"}
        )

    def test_expired_assignment_grants_nothing(self):
        role = create_role(self.org, code="temp", permissions=["users.view"])
        UserRole.objects.create(
            user=self.user, role=role, expires_at=timezone.now() - timedelta(hours=1)
        )

        self.assertEqual(get_user_permission_codes(self.user), set())

    def test_future_expiry_still_grants(self):
        role = create_role(self.org, code="temp2", permissions=["users.view"])
        UserRole.objects.create(
            user=self.user, role=role, expires_at=timezone.now() + timedelta(days=1)
        )

        self.assertEqual(get_user_permission_codes(self.user), {"users.view"})

    def test_soft_deleted_role_grants_nothing(self):
        role = create_role(self.org, code="doomed", permissions=["users.view"])
        grant(self.user, role)
        role.delete()

        self.assertEqual(get_user_permission_codes(self.user), set())

    def test_inactive_user_has_no_permissions(self):
        grant(self.user, create_role(self.org, code="v", permissions=["users.view"]))
        self.user.is_active = False

        self.assertEqual(get_user_permission_codes(self.user), set())

    def test_superuser_holds_every_permission(self):
        root = create_superuser()
        codes = get_user_permission_codes(root)

        self.assertIn("users.delete", codes)
        self.assertIn("audit.view", codes)

    def test_permission_cache_is_cleared_after_a_role_change(self):
        from core.accounts.services import assign_role

        self.assertEqual(self.user.get_permission_codes(), set())
        role = create_role(self.org, code="late", permissions=["users.view"])
        assign_role(user=self.user, role=role)

        self.assertEqual(self.user.get_permission_codes(), {"users.view"})


class UserRoleConstraintTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="constraint-college")
        self.other_org = create_organization(code="other-college")
        self.user = create_user(self.org, email="c@test.edu")

    def test_role_from_another_organization_is_rejected(self):
        foreign_role = create_role(self.other_org, code="foreign")
        assignment = UserRole(user=self.user, role=foreign_role)

        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_campus_from_another_organization_is_rejected(self):
        role = create_role(self.org, code="local")
        foreign_campus = create_campus(self.other_org, code="foreign-campus")
        assignment = UserRole(user=self.user, role=role, campus=foreign_campus)

        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_system_roles_can_be_assigned_to_any_organization(self):
        system_role = Role.objects.get(code="staff", organization=None)
        UserRole(user=self.user, role=system_role).full_clean()

    def test_duplicate_assignment_is_rejected(self):
        from core.common.exceptions import ConflictError
        from core.accounts.services import assign_role

        role = create_role(self.org, code="dup")
        assign_role(user=self.user, role=role)

        with self.assertRaises(ConflictError):
            assign_role(user=self.user, role=role)


class PermissionCatalogueTests(TestCase):
    def test_sync_permissions_is_idempotent(self):
        from django.core.management import call_command

        call_command("sync_permissions", verbosity=0)
        first = Permission.objects.count()
        call_command("sync_permissions", verbosity=0)

        self.assertEqual(Permission.objects.count(), first)

    def test_sync_creates_system_roles_with_permissions(self):
        from django.core.management import call_command

        call_command("sync_permissions", verbosity=0)
        org_admin = Role.objects.get(code="org-admin", organization=None)

        self.assertTrue(org_admin.is_system)
        self.assertEqual(org_admin.permissions.count(), Permission.objects.count())

    def test_permission_module_and_action_derived_from_code(self):
        permission = Permission.objects.create(code="library.issue", name="Issue a book")

        self.assertEqual(permission.module, "library")
        self.assertEqual(permission.action, "issue")


class RoleAPITests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="roleapi-college")
        self.admin = user_with_permissions(
            self.org,
            # users.view too: a role can only be given permissions its
            # creator holds, and the tests below put users.view into roles.
            ["roles.view", "roles.create", "roles.update", "roles.delete", "users.view"],
            email="roleadmin@test.edu",
        )
        self.authenticate(self.admin)

    def test_list_shows_system_roles_and_own_roles(self):
        create_role(self.org, code="local-role")
        response = self.client.get("/api/v1/roles/")

        codes = {row["code"] for row in response.data["results"]}
        self.assertIn("org-admin", codes)
        self.assertIn("local-role", codes)

    def test_roles_of_other_organizations_are_invisible(self):
        other = create_organization(code="hidden-college")
        create_role(other, code="hidden-role")

        response = self.client.get("/api/v1/roles/")
        codes = {row["code"] for row in response.data["results"]}
        self.assertNotIn("hidden-role", codes)

    def test_create_role_binds_it_to_the_callers_organization(self):
        response = self.client.post(
            "/api/v1/roles/",
            {"code": "librarian", "name": "Librarian", "permissions": ["users.view"]},
        )

        self.assertEqual(response.status_code, 201, response.data)
        role = Role.objects.get(code="librarian")
        self.assertEqual(role.organization_id, self.org.pk)
        self.assertFalse(role.is_system)
        self.assertEqual(list(role.permissions.values_list("code", flat=True)), ["users.view"])

    def test_system_roles_cannot_be_modified(self):
        system_role = Role.objects.get(code="staff", organization=None)
        response = self.client.patch(f"/api/v1/roles/{system_role.pk}/", {"name": "Hacked"})

        self.assertEqual(response.status_code, 400)
        system_role.refresh_from_db()
        self.assertEqual(system_role.name, "Staff")

    def test_system_roles_cannot_be_deleted(self):
        system_role = Role.objects.get(code="staff", organization=None)
        response = self.client.delete(f"/api/v1/roles/{system_role.pk}/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Role.objects.filter(pk=system_role.pk).exists())

    def test_duplicate_role_code_within_organization_is_rejected(self):
        create_role(self.org, code="dup-role")
        response = self.client.post("/api/v1/roles/", {"code": "dup-role", "name": "Dup"})

        self.assertEqual(response.status_code, 400)


class PermissionCatalogueAPITests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="catalogue-college")

    def test_catalogue_requires_the_view_permission(self):
        plain = create_user(self.org, email="plain@test.edu")
        self.authenticate(plain)

        self.assertEqual(self.client.get("/api/v1/permissions/").status_code, 403)

    def test_catalogue_is_read_only(self):
        reader = user_with_permissions(
            self.org, ["permissions.view"], email="reader@test.edu"
        )
        self.authenticate(reader)

        self.assertEqual(self.client.get("/api/v1/permissions/").status_code, 200)
        response = self.client.post(
            "/api/v1/permissions/", {"code": "x.y", "name": "X"}
        )
        self.assertEqual(response.status_code, 405)
