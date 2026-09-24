"""Codes typed in upper case are stored lower-case, not refused."""
from tests.base import APITestCaseBase
from tests.factories import create_organization, create_superuser, user_with_system_role


class CodeCaseTests(APITestCaseBase):
    def test_organization_code_is_lower_cased(self):
        self.authenticate(create_superuser())

        response = self.client.post("/api/v1/organizations/", {"name": "Test", "code": "KMC-2"})

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["code"], "kmc-2")

    def test_campus_code_is_lower_cased(self):
        org = create_organization(code="kmc")
        self.authenticate(user_with_system_role(org, "org-admin", email="a@kmc.test"))

        response = self.client.post("/api/v1/campuses/", {"name": "Lalitpur", "code": " Lalitpur "})

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["code"], "lalitpur")

    def test_invalid_characters_are_still_refused(self):
        org = create_organization(code="kmc")
        self.authenticate(user_with_system_role(org, "org-admin", email="a@kmc.test"))

        self.assertEqual(self.client.post("/api/v1/campuses/", {"name": "X", "code": "la lit"}).status_code, 400)
