import io
import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from core.accounts.models import User
from core.organizations.models import Campus, Organization
from core.permissions.models import UserRole
from core.signup.models import SignupRequest
from tests.base import APITestCaseBase
from tests.factories import DEFAULT_PASSWORD, create_organization, create_superuser, create_user, \
    user_with_system_role

API = "/api/v1"
PASSWORD = "Kathmandu-Valley-42"


def link_token(message) -> str:
    return re.search(r"token=([\w-]+)", message.body).group(1)


@override_settings(SIGNUP_ENABLED=True, EMAIL_DELIVERY="django", CAPTCHA_PROVIDER="off",
                   SIGNUP_VERIFY_URL="https://app.test/verify?token={token}",
                   PASSWORD_RESET_URL="https://app.test/reset?uid={uid}&token={token}")
class SignupTestCase(APITestCaseBase):
    def setUp(self):
        cache.clear()

    def body(self, **overrides):
        return {"organization_name": "Himalaya Valley College", "organization_code": "hvc",
                "organization_type": "college", "timezone": "Asia/Kathmandu", "first_name": "Sunita",
                "last_name": "Rai", "email": "sunita@hvc.edu.np", "password": PASSWORD, **overrides}

    def sign_up(self, **overrides):
        return self.client.post(f"{API}/signup/", self.body(**overrides), format="json")

    def verify(self, token):
        return self.client.post(f"{API}/signup/verify/", {"token": token}, format="json")

    def error(self, response):
        return response.data["error"]


class SignupFlowTests(SignupTestCase):
    def test_signup_verify_creates_the_organization_and_signs_in(self):
        response = self.sign_up()
        self.assertEqual(response.status_code, 202, response.data)
        self.assertFalse(Organization.objects.filter(code="hvc").exists())  # nothing until verified
        request = SignupRequest.objects.get()
        self.assertTrue(request.password_hash.startswith(("md5$", "pbkdf2", "argon2")))
        self.assertNotIn(PASSWORD, request.password_hash)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["sunita@hvc.edu.np"])
        token = link_token(mail.outbox[0])
        self.assertNotIn(token, request.token_hash)

        response = self.verify(token)
        self.assertEqual(response.status_code, 201, response.data)
        organization = Organization.objects.get(code="hvc")
        self.assertEqual((organization.name, organization.timezone), ("Himalaya Valley College", "Asia/Kathmandu"))
        self.assertTrue(Campus.objects.filter(organization=organization, is_main=True).exists())
        admin = User.objects.get(email="sunita@hvc.edu.np")
        self.assertEqual((admin.organization, admin.first_name), (organization, "Sunita"))
        self.assertTrue(UserRole.objects.filter(user=admin, role__code="org-admin").exists())
        self.assertEqual(response.data["organization"]["code"], "hvc")

        # Signed in at once, and the chosen password works for later logins.
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        self.assertEqual(self.client.get(f"{API}/campuses/").status_code, 200)
        self.client.credentials()
        login = self.client.post(f"{API}/auth/login/", {"email": "sunita@hvc.edu.np", "password": PASSWORD})
        self.assertEqual(login.status_code, 200, login.data)

        request.refresh_from_db()
        self.assertEqual((request.status, request.organization, request.password_hash),
                         ("completed", organization, ""))
        self.assertEqual(self.error(self.verify(token))["code"], "invalid_token")  # single use

    def test_closed_unless_enabled(self):
        with override_settings(SIGNUP_ENABLED=False):
            response = self.sign_up()
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.error(response)["code"], "signup_disabled")
            config = self.client.get(f"{API}/signup/config/")
            self.assertEqual(config.status_code, 200)
            self.assertFalse(config.data["enabled"])

    def test_config_names_the_captcha_widget(self):
        with override_settings(CAPTCHA_PROVIDER="turnstile", CAPTCHA_SITE_KEY="site-123", CAPTCHA_SECRET_KEY="x"):
            data = self.client.get(f"{API}/signup/config/").data
        self.assertEqual((data["captcha_provider"], data["captcha_site_key"]), ("turnstile", "site-123"))
        self.assertIn({"value": "school", "label": "School"}, data["organization_types"])

    def test_a_stray_authorization_header_is_ignored(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer garbage")
        self.assertEqual(self.sign_up().status_code, 202)

    def test_existing_account_gets_a_note_not_a_signup(self):
        org = create_organization(code="kmc")
        create_user(org, email="sunita@hvc.edu.np")
        response = self.sign_up(email="Sunita@HVC.edu.np")
        self.assertEqual(response.status_code, 202)  # same answer: nothing revealed
        self.assertFalse(SignupRequest.objects.exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("already has an account", mail.outbox[0].body)

    def test_expired_link_and_resend(self):
        self.sign_up()
        old = link_token(mail.outbox[0])
        SignupRequest.objects.update(token_expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.error(self.verify(old))["code"], "expired_token")

        response = self.client.post(f"{API}/signup/resend/", {"email": "sunita@hvc.edu.np"}, format="json")
        self.assertEqual(response.status_code, 202)
        new = link_token(mail.outbox[1])
        self.assertEqual(self.error(self.verify(old))["code"], "invalid_token")  # replaced
        self.assertEqual(self.verify(new).status_code, 201)

        # Nothing pending: same answer, nothing sent.
        response = self.client.post(f"{API}/signup/resend/", {"email": "nobody@example.com"}, format="json")
        self.assertEqual((response.status_code, len(mail.outbox)), (202, 2))

    def test_a_second_signup_replaces_the_first(self):
        self.sign_up()
        first = link_token(mail.outbox[0])
        self.sign_up(organization_code="hvc-2")
        self.assertEqual(SignupRequest.objects.count(), 1)
        self.assertEqual(self.error(self.verify(first))["code"], "invalid_token")
        self.assertEqual(self.verify(link_token(mail.outbox[1])).status_code, 201)
        self.assertTrue(Organization.objects.filter(code="hvc-2").exists())

    def test_code_taken_while_waiting_for_verification(self):
        self.sign_up()
        token = link_token(mail.outbox[0])
        # An unverified signup holds nothing: someone else verifies the code first.
        self.sign_up(email="other@hvc.edu.np")
        self.assertEqual(self.verify(link_token(mail.outbox[1])).status_code, 201)
        response = self.verify(token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error(response)["code"], "code_taken")

    def test_simultaneous_verification_is_a_conflict_not_a_crash(self):
        from django.db import IntegrityError

        self.sign_up()
        with mock.patch("core.signup.services.create_organization", side_effect=IntegrityError("unique")):
            response = self.verify(link_token(mail.outbox[0]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(SignupRequest.objects.get().status, "pending")

    def test_bad_input(self):
        create_organization(code="kmc")
        cases = {
            "organization_code": [{"organization_code": "kmc"}, {"organization_code": "admin"},
                                  {"organization_code": "Bad Code!"}, {"organization_code": "x"}],
            "email": [{"email": "a@mailinator.com"}, {"email": "a@inbox.yopmail.com"}, {"email": "nope"}],
            "password": [{"password": "short"}, {"password": "sunita@hvc.edu.np"}, {"password": "12345678901"}],
            "timezone": [{"timezone": "Mars/Olympus"}],
        }
        for field, overrides in cases.items():
            for override in overrides:
                response = self.sign_up(**override)
                self.assertEqual(response.status_code, 400, override)
                self.assertIn(field, self.error(response)["details"], override)
        self.assertFalse(SignupRequest.objects.exists())
        self.assertEqual(mail.outbox, [])

    @override_settings(SIGNUP_BLOCKED_EMAIL_DOMAINS=["junk.example"])
    def test_extra_blocked_domains(self):
        self.assertEqual(self.sign_up(email="a@junk.example").status_code, 400)

    def test_check_code(self):
        create_organization(code="kmc")

        def check(code):
            response = self.client.get(f"{API}/signup/check-code/", {"code": code})
            self.assertEqual(response.status_code, 200)
            return response.data["available"], response.data["reason"]

        self.assertEqual(check("hvc"), (True, None))
        self.assertEqual(check("KMC"), (False, "taken"))
        self.assertEqual(check("www"), (False, "reserved"))
        self.assertEqual(check("-bad-"), (False, "invalid"))
        self.assertEqual(self.client.get(f"{API}/signup/check-code/").status_code, 400)

    def test_mail_per_address_is_capped(self):
        with override_settings(EMAILS_PER_ADDRESS_PER_HOUR=2):
            for _ in range(4):
                self.assertEqual(self.client.post(f"{API}/signup/resend/", {"email": "x@y.com"}).status_code, 202)
                self.sign_up()
        self.assertEqual(len(mail.outbox), 2)

    def test_purge_command(self):
        self.sign_up()
        self.assertEqual(self.sign_up(email="b@hvc.edu.np", organization_code="bee").status_code, 202)
        SignupRequest.objects.filter(admin_email="b@hvc.edu.np").update(
            token_expires_at=timezone.now() - timedelta(days=8))
        out = io.StringIO()
        call_command("purge_signup_requests", stdout=out)
        self.assertIn("Deleted 1", out.getvalue())
        self.assertEqual(list(SignupRequest.objects.values_list("admin_email", flat=True)), ["sunita@hvc.edu.np"])


class CaptchaTests(SignupTestCase):
    @override_settings(CAPTCHA_PROVIDER="turnstile", CAPTCHA_SECRET_KEY="secret")
    def test_signup_and_resend_need_a_passing_captcha(self):
        with mock.patch("integrations.captcha.base._siteverify", return_value=False):
            for response in (self.sign_up(captcha_token="t"), self.sign_up(),
                             self.client.post(f"{API}/signup/resend/", {"email": "a@b.com", "captcha_token": "t"})):
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.error(response)["code"], "captcha_failed")
        self.assertFalse(SignupRequest.objects.exists())
        with mock.patch("integrations.captcha.base._siteverify", return_value=True) as check:
            self.assertEqual(self.sign_up(captcha_token="t").status_code, 202)
        self.assertEqual(check.call_args.args[1], "t")

    @override_settings(CAPTCHA_PROVIDER="recaptcha", CAPTCHA_SECRET_KEY="secret", CAPTCHA_MIN_SCORE=0.5)
    def test_provider_protocol(self):
        import io
        import json
        import urllib.error

        from integrations.captcha import base as captcha

        def answer(payload):
            return mock.MagicMock(__enter__=lambda s: io.BytesIO(json.dumps(payload).encode()))

        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value = answer({"success": True, "score": 0.9})
            self.assertTrue(captcha.verify("tok", "1.2.3.4"))
            sent = urlopen.call_args.args[0]
            self.assertEqual(sent.full_url, captcha.VERIFY_URLS["recaptcha"])
            self.assertIn(b"remoteip=1.2.3.4", sent.data)
            urlopen.return_value = answer({"success": True, "score": 0.1})  # v3: a likely bot
            self.assertFalse(captcha.verify("tok"))
            urlopen.return_value = answer({"success": False})
            self.assertFalse(captcha.verify("tok"))
            urlopen.side_effect = urllib.error.URLError("down")  # fails closed
            self.assertFalse(captcha.verify("tok"))


@override_settings(SIGNUP_REQUIRE_APPROVAL=True)
class ApprovalTests(SignupTestCase):
    def setUp(self):
        super().setUp()
        self.root = create_superuser()

    def verified(self, **overrides):
        self.sign_up(**overrides)
        response = self.verify(link_token(mail.outbox[-1]))
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(response.data["status"], "awaiting_approval")
        return SignupRequest.objects.get(admin_email=self.body(**overrides)["email"])

    def test_approve(self):
        request = self.verified()
        self.assertFalse(Organization.objects.filter(code="hvc").exists())
        self.assertIn("root@platform.test", [m.to[0] for m in mail.outbox])  # platform admin told
        # A verified request holds its code and email.
        self.assertFalse(self.client.get(f"{API}/signup/check-code/", {"code": "hvc"}).data["available"])
        self.assertEqual(self.sign_up(email="x@hvc.edu.np").status_code, 400)

        self.authenticate(self.root)
        listed = self.client.get(f"{API}/signup-requests/", {"status": "awaiting_approval"})
        self.assertEqual([r["id"] for r in listed.data["results"]], [request.pk])
        self.assertNotIn("password_hash", listed.data["results"][0])
        response = self.client.post(f"{API}/signup-requests/{request.pk}/approve/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(mail.outbox[-1].to, ["sunita@hvc.edu.np"])
        self.assertEqual(self.client.post(f"{API}/signup-requests/{request.pk}/approve/").status_code, 409)
        self.client.credentials()
        login = self.client.post(f"{API}/auth/login/", {"email": "sunita@hvc.edu.np", "password": PASSWORD})
        self.assertEqual(login.status_code, 200)

    def test_reject(self):
        request = self.verified()
        self.authenticate(self.root)
        response = self.client.post(f"{API}/signup-requests/{request.pk}/reject/", {"reason": "Test account"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "rejected")
        self.assertIn("Test account", mail.outbox[-1].body)
        self.assertFalse(Organization.objects.filter(code="hvc").exists())
        self.assertEqual(self.client.post(f"{API}/signup-requests/{request.pk}/approve/").status_code, 409)
        # The code is free again.
        self.assertTrue(self.client.get(f"{API}/signup/check-code/", {"code": "hvc"}).data["available"])

    def test_only_platform_admins(self):
        request = self.verified()
        org_admin = user_with_system_role(create_organization(code="kmc"), "org-admin", email="a@kmc.test")
        self.authenticate(org_admin)
        self.assertEqual(self.client.get(f"{API}/signup-requests/").status_code, 403)
        self.assertEqual(self.client.post(f"{API}/signup-requests/{request.pk}/approve/").status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.get(f"{API}/signup-requests/").status_code, 401)


@override_settings(EMAIL_DELIVERY="django", PASSWORD_RESET_URL="https://app.test/reset?uid={uid}&token={token}")
class PasswordResetTests(APITestCaseBase):
    def setUp(self):
        cache.clear()
        self.org = create_organization(code="kmc")
        self.user = create_user(self.org, email="ram@kmc.test")

    def ask(self, email):
        response = self.client.post(f"{API}/auth/password-reset/", {"email": email}, format="json")
        self.assertEqual(response.status_code, 202)

    def confirm(self, password=PASSWORD, message=None):
        found = re.search(r"uid=([\w-]+)&token=([\w-]+)", (message or mail.outbox[-1]).body)
        return self.client.post(f"{API}/auth/password-reset/confirm/",
                                {"uid": found.group(1), "token": found.group(2), "new_password": password},
                                format="json")

    def login(self, password):
        return self.client.post(f"{API}/auth/login/", {"email": "ram@kmc.test", "password": password})

    def test_reset_signs_out_everywhere_and_works_once(self):
        refresh = self.login(DEFAULT_PASSWORD).data["refresh"]
        self.ask("RAM@kmc.test")
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        response = self.confirm()
        self.assertEqual(response.status_code, 204, response.data)
        self.assertEqual(self.login(PASSWORD).status_code, 200)
        self.assertEqual(self.login(DEFAULT_PASSWORD).status_code, 400)
        self.assertEqual(self.client.post(f"{API}/auth/refresh/", {"refresh": refresh}).status_code, 401)
        response = self.confirm("Another-Password-77", message)  # same link again
        self.assertEqual(response.data["error"]["code"], "invalid_token")

    def test_nothing_revealed_or_sent_for_unknown_inactive_or_key_users(self):
        self.ask("nobody@kmc.test")
        inactive = create_user(self.org, email="gone@kmc.test", is_active=False)
        self.ask(inactive.email)
        create_user(self.org, email="key-abc@api-keys.invalid", user_type=User.Type.INTEGRATION)
        self.ask("key-abc@api-keys.invalid")
        closed = create_organization(code="closed", is_active=False)
        create_user(closed, email="x@closed.test")
        self.ask("x@closed.test")
        self.assertEqual(mail.outbox, [])

    def test_bad_link_and_weak_password(self):
        self.ask("ram@kmc.test")
        response = self.confirm("short")
        self.assertEqual(response.status_code, 400)
        self.assertIn("new_password", response.data["error"]["details"])
        for uid, token in (("garbage", "x"), ("", ""), ("MQ", "1-abc")):
            response = self.client.post(f"{API}/auth/password-reset/confirm/",
                                        {"uid": uid, "token": token, "new_password": PASSWORD}, format="json")
            self.assertEqual(response.status_code, 400, (uid, token))

    def test_reset_clears_a_lockout(self):
        with override_settings(LOGIN_LOCKOUT_ATTEMPTS=2):
            self.login("wrong")
            self.login("wrong")
            self.assertEqual(self.login(DEFAULT_PASSWORD).status_code, 429)
            self.ask("ram@kmc.test")
            self.assertEqual(self.confirm().status_code, 204)
            self.assertEqual(self.login(PASSWORD).status_code, 200)
