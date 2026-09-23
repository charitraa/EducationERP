"""CORS behaviour.

The SPA lives on its own origin, so a broken preflight silently breaks every
API call in the browser while the test suite stays green. These lock down the
allow-list, the preflight response and the headers on real responses.
"""
from django.test import SimpleTestCase, override_settings

ALLOWED = "https://portal.example.edu"
DENIED = "https://attacker.example.com"


@override_settings(CORS_ALLOWED_ORIGINS=[ALLOWED])
class CorsTests(SimpleTestCase):
    def preflight(self, origin, path="/api/v1/auth/login/", method="POST"):
        return self.client.options(
            path,
            HTTP_ORIGIN=origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD=method,
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type",
        )

    def test_preflight_from_allowed_origin_is_answered(self):
        response = self.preflight(ALLOWED)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["access-control-allow-origin"], ALLOWED)
        self.assertIn("authorization", response["access-control-allow-headers"])
        self.assertIn("POST", response["access-control-allow-methods"])

    def test_preflight_from_unknown_origin_gets_no_allow_header(self):
        # django-cors-headers still returns a response; the missing header is
        # what makes the browser block the call.
        response = self.preflight(DENIED)

        self.assertNotIn("access-control-allow-origin", response)

    def test_error_responses_still_carry_cors_headers(self):
        # Without this the SPA sees an opaque network error instead of the 401
        # body, which is the classic "CORS bug" that is really an auth bug.
        response = self.client.get("/api/v1/auth/me/", HTTP_ORIGIN=ALLOWED)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["access-control-allow-origin"], ALLOWED)

    def test_admin_is_not_exposed_cross_origin(self):
        # CORS_URLS_REGEX limits the headers to /api/.
        response = self.client.get("/admin/login/", HTTP_ORIGIN=ALLOWED)

        self.assertNotIn("access-control-allow-origin", response)
