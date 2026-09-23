from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction

from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_superuser,
    user_with_system_role,
)

from ..models import Campus, Organization

ORGS_URL = "/api/v1/organizations/"
CAMPUSES_URL = "/api/v1/campuses/"


class OrganizationLifecycleTests(APITestCaseBase):
    """Onboarding and removing tenants is a platform-level action."""

    def setUp(self):
        self.org = create_organization(code="kmc")
        self.principal = user_with_system_role(self.org, "org-admin", email="principal@kmc.test")

    def test_org_admin_cannot_create_an_organization(self):
        self.authenticate(self.principal)

        response = self.client.post(ORGS_URL, {"name": "Stray College", "code": "stray"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Organization.all_objects.filter(code="stray").exists())

    def test_org_admin_cannot_delete_their_own_organization(self):
        self.authenticate(self.principal)

        response = self.client.delete(f"{ORGS_URL}{self.org.pk}/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Organization.objects.filter(pk=self.org.pk).exists())

    def test_org_admin_can_still_view_and_update_their_organization(self):
        self.authenticate(self.principal)

        response = self.client.patch(f"{ORGS_URL}{self.org.pk}/", {"phone": "01-5550000"})

        self.assertEqual(response.status_code, 200, response.data)

    def test_platform_admin_can_create_and_delete_organizations(self):
        self.authenticate(create_superuser())

        created = self.client.post(ORGS_URL, {"name": "New College", "code": "new-college"})
        deleted = self.client.delete(f"{ORGS_URL}{created.data['id']}/")

        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(deleted.status_code, 204)


class PlatformAdminCampusTests(APITestCaseBase):
    """Superusers belong to no organization, so they must say which one."""

    def setUp(self):
        self.org = create_organization(code="kmc")
        self.authenticate(create_superuser())

    def test_creates_a_campus_in_the_named_organization(self):
        response = self.client.post(
            CAMPUSES_URL, {"name": "Lalitpur", "code": "lalitpur", "organization": self.org.pk}
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Campus.objects.get(code="lalitpur").organization, self.org)

    def test_missing_organization_is_a_validation_error_not_a_crash(self):
        response = self.client.post(CAMPUSES_URL, {"name": "Lalitpur", "code": "lalitpur"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("organization", response.data["error"]["details"])

    def test_unknown_organization_is_rejected(self):
        response = self.client.post(
            CAMPUSES_URL, {"name": "Lalitpur", "code": "lalitpur", "organization": 999999}
        )

        self.assertEqual(response.status_code, 400)


class MainCampusTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.main = create_campus(self.org, code="main", is_main=True)
        self.authenticate(user_with_system_role(self.org, "org-admin", email="p@kmc.test"))

    def test_a_second_main_campus_is_rejected(self):
        response = self.client.post(
            CAMPUSES_URL, {"name": "Lalitpur", "code": "lalitpur", "is_main": True}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("is_main", response.data["error"]["details"])

    def test_main_can_move_once_the_old_one_is_unset(self):
        other = create_campus(self.org, code="lalitpur")

        self.client.patch(f"{CAMPUSES_URL}{self.main.pk}/", {"is_main": False})
        response = self.client.patch(f"{CAMPUSES_URL}{other.pk}/", {"is_main": True})

        self.assertEqual(response.status_code, 200, response.data)

    def test_database_also_refuses_two_main_campuses(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_campus(self.org, code="second-main", is_main=True)

    def test_each_organization_has_its_own_main_campus(self):
        other_org = create_organization(code="sunrise")

        create_campus(other_org, code="main", is_main=True)  # no error


class CampusScopedRoleTests(APITestCaseBase):
    """A campus-admin granted for one campus manages that campus only."""

    def setUp(self):
        self.org = create_organization(code="kmc")
        self.lalitpur = create_campus(self.org, code="lalitpur", name="Lalitpur")
        self.bhaktapur = create_campus(self.org, code="bhaktapur", name="Bhaktapur")
        self.ram = user_with_system_role(
            self.org, "campus-admin", email="ram@kmc.test", campus=self.lalitpur
        )
        self.authenticate(self.ram)

    def test_lists_only_their_campus(self):
        response = self.client.get(CAMPUSES_URL)

        self.assertEqual([row["name"] for row in response.data["results"]], ["Lalitpur"])

    def test_can_update_their_campus(self):
        response = self.client.patch(f"{CAMPUSES_URL}{self.lalitpur.pk}/", {"city": "Patan"})

        self.assertEqual(response.status_code, 200)

    def test_cannot_see_or_update_another_campus(self):
        url = f"{CAMPUSES_URL}{self.bhaktapur.pk}/"

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, {"city": "X"}).status_code, 404)

    def test_organization_wide_holder_sees_every_campus(self):
        self.authenticate(user_with_system_role(self.org, "campus-admin", email="all@kmc.test"))

        response = self.client.get(CAMPUSES_URL)

        self.assertEqual(response.data["count"], 2)

    def test_creating_a_campus_needs_an_organization_wide_role(self):
        from core.permissions.models import Role

        scoped_creator = user_with_system_role(
            self.org, "org-admin", email="scoped@kmc.test", campus=self.lalitpur
        )
        self.assertTrue(Role.objects.get(code="org-admin").permissions.filter(code="campuses.create").exists())
        self.authenticate(scoped_creator)

        response = self.client.post(CAMPUSES_URL, {"name": "Kirtipur", "code": "kirtipur"})

        self.assertEqual(response.status_code, 403)


class BootstrapOrganizationTests(APITestCaseBase):
    def test_unknown_type_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command(
                "bootstrap_organization",
                name="Sunrise", code="sunrise", type="skool",
                admin_email="p@sunrise.test", stdout=StringIO(),
            )

        self.assertFalse(Organization.objects.filter(code="sunrise").exists())
