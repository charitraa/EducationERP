from django.test import TestCase
from rest_framework.test import APIClient

from tests.base import APITestCaseBase
from tests.factories import create_organization, user_with_permissions


class HealthEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_health_is_public(self):
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ok")

    def test_ready_checks_the_database(self):
        response = self.client.get("/ready/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["checks"]["database"], "ok")

    def test_ready_checks_the_cache(self):
        response = self.client.get("/ready/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["checks"]["cache"], "ok")


class ErrorEnvelopeTests(APITestCaseBase):
    """Every failure uses the same response shape so clients can rely on it."""

    def setUp(self):
        self.org = create_organization(code="err-college")
        self.user = user_with_permissions(
            self.org, ["campuses.view", "campuses.create"], email="err@test.edu"
        )
        self.authenticate(self.user)

    def test_validation_error_shape(self):
        response = self.client.post("/api/v1/campuses/", {"name": ""})

        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)
        self.assertIn("code", response.data["error"])
        self.assertIn("message", response.data["error"])
        self.assertIn("details", response.data["error"])

    def test_not_found_shape(self):
        response = self.client.get("/api/v1/campuses/999999/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["error"]["code"], "not_found")

    def test_permission_denied_shape(self):
        response = self.client.delete("/api/v1/campuses/1/")

        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.data)


class APIVersioningTests(APITestCaseBase):
    def test_v1_prefix_is_live(self):
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 401)  # reached the view, not a 404

    def test_unversioned_path_is_not_served(self):
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 404)


class SchemaTests(APITestCaseBase):
    def test_openapi_schema_generates(self):
        response = self.client.get("/api/schema/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"openapi", response.content[:200].lower())
