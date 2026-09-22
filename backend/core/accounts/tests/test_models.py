from django.db import IntegrityError
from django.test import TestCase

from tests.factories import create_organization, create_superuser, create_user

from ..models import User


class UserModelTests(TestCase):
    def setUp(self):
        self.org = create_organization(code="acme")

    def test_email_is_normalized_to_lowercase(self):
        user = create_user(self.org, email="Mixed.Case@Test.EDU")
        self.assertEqual(user.email, "mixed.case@test.edu")

    def test_email_must_be_unique(self):
        create_user(self.org, email="dup@test.edu")
        with self.assertRaises(IntegrityError):
            create_user(self.org, email="dup@test.edu")

    def test_password_is_hashed_not_stored_plaintext(self):
        user = create_user(self.org, email="hash@test.edu", password="Secret-pass-123")
        self.assertNotEqual(user.password, "Secret-pass-123")
        self.assertTrue(user.check_password("Secret-pass-123"))

    def test_user_requires_email(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(email="", password="x")

    def test_full_name_falls_back_to_email(self):
        user = create_user(self.org, email="noname@test.edu")
        self.assertEqual(user.get_full_name(), "noname@test.edu")

        user.first_name, user.last_name = "Ada", "Lovelace"
        self.assertEqual(user.get_full_name(), "Ada Lovelace")

    def test_superuser_has_no_organization_and_is_platform_admin(self):
        root = create_superuser()
        self.assertIsNone(root.organization_id)
        self.assertTrue(root.is_platform_admin)
        self.assertTrue(root.is_staff)

    def test_org_scoped_superuser_is_not_a_platform_admin(self):
        """A superuser bound to a tenant must not gain cross-tenant reach."""
        user = create_user(self.org, email="orgroot@test.edu", is_superuser=True)
        self.assertFalse(user.is_platform_admin)

    def test_soft_deleted_user_hidden_from_default_manager(self):
        user = create_user(self.org, email="gone@test.edu")
        user.delete()

        self.assertFalse(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(User.all_objects.filter(pk=user.pk).exists())
