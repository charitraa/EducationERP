"""Shared test case base.

The permission catalogue is code-declared, so tests sync it once per class
rather than hand-creating Permission rows.
"""
from django.core.management import call_command
from rest_framework.test import APITestCase

from .factories import DEFAULT_PASSWORD


class APITestCaseBase(APITestCase):
    """API test case with the permission registry loaded and JWT helpers."""

    @classmethod
    def setUpTestData(cls):
        call_command("sync_permissions", verbosity=0)

    def authenticate(self, user):
        """Log in over the real endpoint so the whole auth path is exercised."""
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": DEFAULT_PASSWORD},
        )
        assert response.status_code == 200, response.data
        token = response.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return token

    def logout(self):
        self.client.credentials()
