from unittest import mock

from core.audit.models import AuditLog
from tests.base import APITestCaseBase
from tests.factories import DEFAULT_PASSWORD, create_organization, create_user

LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
ME_URL = "/api/v1/auth/me/"
CHANGE_PASSWORD_URL = "/api/v1/auth/change-password/"


class LoginTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="login-college")
        self.user = create_user(self.org, email="teacher@test.edu", first_name="Ada")

    def test_login_returns_token_pair_and_profile(self):
        response = self.client.post(
            LOGIN_URL, {"email": self.user.email, "password": DEFAULT_PASSWORD}
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["email"], self.user.email)
        self.assertEqual(response.data["user"]["organization"]["code"], "login-college")

    def test_login_is_case_insensitive_on_email(self):
        response = self.client.post(
            LOGIN_URL, {"email": "TEACHER@TEST.EDU", "password": DEFAULT_PASSWORD}
        )
        self.assertEqual(response.status_code, 200)

    def test_wrong_password_is_rejected(self):
        response = self.client.post(
            LOGIN_URL, {"email": self.user.email, "password": "wrong-password"}
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_email_gives_the_same_error_as_a_wrong_password(self):
        """Login must not reveal which accounts exist."""
        unknown = self.client.post(
            LOGIN_URL, {"email": "nobody@test.edu", "password": DEFAULT_PASSWORD}
        )
        wrong = self.client.post(
            LOGIN_URL, {"email": self.user.email, "password": "wrong-password"}
        )

        self.assertEqual(unknown.status_code, wrong.status_code)
        self.assertEqual(unknown.data, wrong.data)

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()

        response = self.client.post(
            LOGIN_URL, {"email": self.user.email, "password": DEFAULT_PASSWORD}
        )
        self.assertEqual(response.status_code, 400)

    def test_successful_login_is_audited(self):
        self.client.post(LOGIN_URL, {"email": self.user.email, "password": DEFAULT_PASSWORD})

        entry = AuditLog.objects.filter(action=AuditLog.Action.LOGIN).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_id, self.user.pk)

    def test_failed_login_is_audited_without_the_password(self):
        self.client.post(LOGIN_URL, {"email": self.user.email, "password": "wrong-password"})

        entry = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.metadata.get("email"), self.user.email)
        self.assertNotIn("wrong-password", str(entry.metadata))

    def test_failed_login_error_is_readable(self):
        """Regression: the message used to be "[ErrorDetail(string=...)]"."""
        response = self.client.post(LOGIN_URL, {"email": self.user.email, "password": "wrong"})

        error = response.data["error"]
        self.assertEqual(error["message"], "Invalid credentials or inactive account.")
        self.assertEqual(error["code"], "invalid_credentials")

    def test_failed_login_is_filed_under_the_accounts_organization(self):
        """So the organization's admins can see password guessing."""
        self.client.post(LOGIN_URL, {"email": self.user.email, "password": "wrong"})
        self.client.post(LOGIN_URL, {"email": "nobody@nowhere.test", "password": "wrong"})

        entries = AuditLog.objects.filter(action=AuditLog.Action.LOGIN_FAILED)
        by_email = {e.metadata["email"]: e.organization_id for e in entries}
        self.assertEqual(by_email[self.user.email], self.user.organization_id)
        self.assertIsNone(by_email["nobody@nowhere.test"])

    def test_login_is_recorded_once_per_attempt(self):
        """Regression: the view must not authenticate twice."""
        self.client.post(LOGIN_URL, {"email": self.user.email, "password": DEFAULT_PASSWORD})
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.LOGIN).count(), 1)

    def test_login_is_rate_limited(self):
        """Repeated attempts from one client are throttled, not answered forever."""
        from django.core.cache import cache
        from rest_framework.throttling import ScopedRateThrottle

        cache.clear()
        with mock.patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"login": "3/min"}):
            statuses = [
                self.client.post(
                    LOGIN_URL, {"email": self.user.email, "password": "wrong"}
                ).status_code
                for _ in range(5)
            ]
        cache.clear()

        self.assertIn(429, statuses)


class TokenLifecycleTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="token-college")
        self.user = create_user(self.org, email="token@test.edu")
        self.tokens = self.client.post(
            LOGIN_URL, {"email": self.user.email, "password": DEFAULT_PASSWORD}
        ).data

    def test_refresh_returns_a_new_access_token(self):
        response = self.client.post(REFRESH_URL, {"refresh": self.tokens["refresh"]})

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_refresh_rejects_a_garbage_token(self):
        response = self.client.post(REFRESH_URL, {"refresh": "not-a-token"})
        self.assertEqual(response.status_code, 401)

    def test_logout_blacklists_the_refresh_token(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.tokens['access']}")
        response = self.client.post(LOGOUT_URL, {"refresh": self.tokens["refresh"]})
        self.assertEqual(response.status_code, 204)

        reuse = self.client.post(REFRESH_URL, {"refresh": self.tokens["refresh"]})
        self.assertEqual(reuse.status_code, 401)

    def test_logout_requires_authentication(self):
        response = self.client.post(LOGOUT_URL, {"refresh": self.tokens["refresh"]})
        self.assertEqual(response.status_code, 401)


class CurrentUserTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="me-college")
        self.user = create_user(self.org, email="me@test.edu", first_name="Grace")

    def test_me_requires_authentication(self):
        self.assertEqual(self.client.get(ME_URL).status_code, 401)

    def test_me_returns_identity_roles_and_permissions(self):
        self.authenticate(self.user)
        response = self.client.get(ME_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], self.user.email)
        self.assertEqual(response.data["roles"], [])
        self.assertEqual(response.data["permissions"], [])

    def test_user_can_update_own_contact_details(self):
        self.authenticate(self.user)
        response = self.client.patch(ME_URL, {"first_name": "Ada", "phone": "+97712345"})

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Ada")

    def test_user_cannot_escalate_privileges_through_me(self):
        self.authenticate(self.user)
        self.client.patch(ME_URL, {"is_superuser": True, "user_type": "administrator"})

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)
        self.assertNotEqual(self.user.user_type, "administrator")


class ChangePasswordTests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="pwd-college")
        self.user = create_user(self.org, email="pwd@test.edu")
        self.authenticate(self.user)

    def test_password_change_succeeds_and_takes_effect(self):
        response = self.client.post(
            CHANGE_PASSWORD_URL,
            {"current_password": DEFAULT_PASSWORD, "new_password": "Brand-new-pass-99"},
        )
        self.assertEqual(response.status_code, 204)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Brand-new-pass-99"))

    def test_wrong_current_password_is_rejected(self):
        response = self.client.post(
            CHANGE_PASSWORD_URL,
            {"current_password": "nope", "new_password": "Brand-new-pass-99"},
        )
        self.assertEqual(response.status_code, 400)

    def test_weak_password_is_rejected(self):
        response = self.client.post(
            CHANGE_PASSWORD_URL,
            {"current_password": DEFAULT_PASSWORD, "new_password": "123"},
        )
        self.assertEqual(response.status_code, 400)

    def test_password_change_is_audited(self):
        self.client.post(
            CHANGE_PASSWORD_URL,
            {"current_password": DEFAULT_PASSWORD, "new_password": "Brand-new-pass-99"},
        )
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.PASSWORD_CHANGE).exists()
        )
