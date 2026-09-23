"""Editing roles must not hand out permissions the editor doesn't hold."""
from tests.base import APITestCaseBase
from tests.factories import create_organization, create_role, user_with_permissions

ROLES_URL = "/api/v1/roles/"


class RoleEditingEscalationTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        # Can manage roles, but cannot read the audit log.
        self.editor = user_with_permissions(
            self.org,
            ["roles.view", "roles.create", "roles.update", "roles.delete", "users.view"],
            email="editor@kmc.test",
        )
        self.authenticate(self.editor)

    def test_cannot_create_a_role_with_permissions_they_lack(self):
        response = self.client.post(
            ROLES_URL,
            {"code": "auditor", "name": "Auditor", "permissions": ["audit.view"]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("audit.view", str(response.data["error"]["details"]["permissions"]))

    def test_cannot_add_permissions_they_lack_to_an_existing_role(self):
        # Including a role they hold themselves — the classic self-upgrade.
        own_role = self.editor.role_assignments.get().role

        response = self.client.patch(
            f"{ROLES_URL}{own_role.pk}/",
            {"permissions": ["roles.view", "audit.view"]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("audit.view", own_role.permissions.values_list("code", flat=True))

    def test_cannot_edit_or_delete_a_role_carrying_permissions_they_lack(self):
        auditor = create_role(self.org, code="auditor", permissions=["audit.view"])

        edit = self.client.patch(f"{ROLES_URL}{auditor.pk}/", {"name": "Renamed"})
        delete = self.client.delete(f"{ROLES_URL}{auditor.pk}/")

        self.assertEqual(edit.status_code, 400)
        self.assertEqual(delete.status_code, 403)

    def test_can_manage_roles_within_their_own_permissions(self):
        response = self.client.post(
            ROLES_URL,
            {"code": "reader", "name": "Reader", "permissions": ["users.view"]},
        )

        self.assertEqual(response.status_code, 201, response.data)
