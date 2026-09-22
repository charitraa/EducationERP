from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from tests.factories import create_campus, create_organization

from ..models import Campus, Organization


class OrganizationModelTests(TestCase):
    def test_code_must_be_unique(self):
        create_organization(code="alpha")
        with self.assertRaises(IntegrityError):
            create_organization(code="alpha", name="Another")

    def test_code_rejects_uppercase_and_spaces(self):
        org = Organization(name="Bad", code="Not Valid")
        with self.assertRaises(ValidationError):
            org.full_clean()

    def test_soft_delete_hides_row_from_default_manager(self):
        org = create_organization(code="gamma")
        org.delete()

        self.assertFalse(Organization.objects.filter(pk=org.pk).exists())
        self.assertTrue(Organization.all_objects.filter(pk=org.pk).exists())
        self.assertIsNotNone(Organization.all_objects.get(pk=org.pk).deleted_at)

    def test_restore_brings_row_back(self):
        org = create_organization(code="delta")
        org.delete()
        Organization.all_objects.get(pk=org.pk).restore()

        self.assertTrue(Organization.objects.filter(pk=org.pk).exists())

    def test_timestamps_are_populated(self):
        org = create_organization(code="epsilon")
        self.assertIsNotNone(org.created_at)
        self.assertIsNotNone(org.updated_at)


class CampusModelTests(TestCase):
    def setUp(self):
        self.org = create_organization(code="zeta")

    def test_campus_code_unique_within_organization(self):
        create_campus(self.org, code="main")
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_campus(self.org, code="main")

    def test_same_campus_code_allowed_in_different_organizations(self):
        other = create_organization(code="eta")
        create_campus(self.org, code="main")
        create_campus(other, code="main")

        self.assertEqual(Campus.objects.filter(code="main").count(), 2)

    def test_code_freed_after_soft_delete(self):
        """The unique constraint ignores soft-deleted rows."""
        campus = create_campus(self.org, code="main")
        campus.delete()
        create_campus(self.org, code="main")

        self.assertEqual(Campus.objects.filter(organization=self.org).count(), 1)

    def test_deleting_organization_cascades_to_campuses(self):
        create_campus(self.org, code="main")
        self.org.hard_delete()

        self.assertEqual(Campus.all_objects.filter(organization_id=self.org.pk).count(), 0)
