from django.test import TestCase

from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_user,
    user_with_permissions,
)

from ..models import AuditLog
from ..services import diff, log, snapshot


class AuditServiceTests(TestCase):
    def setUp(self):
        self.org = create_organization(code="svc-college")

    def test_snapshot_excludes_sensitive_fields(self):
        user = create_user(self.org, email="snap@test.edu")
        data = snapshot(user)

        self.assertNotIn("password", data)
        self.assertEqual(data["email"], "snap@test.edu")

    def test_diff_reports_only_changed_fields(self):
        before = {"name": "Old", "code": "same"}
        after = {"name": "New", "code": "same"}

        self.assertEqual(diff(before, after), {"name": {"before": "Old", "after": "New"}})

    def test_entries_are_immutable(self):
        entry = log(AuditLog.Action.CREATE, instance=self.org, module="organizations")

        entry.object_repr = "tampered"
        with self.assertRaises(ValueError):
            entry.save()

    def test_logging_never_raises_on_failure(self):
        """A broken audit write must not fail the user's actual operation.

        It must still be reported, so the failure is asserted to reach the log
        rather than vanishing silently.
        """
        from unittest import mock

        with mock.patch.object(AuditLog, "save", side_effect=RuntimeError("db down")):
            with self.assertLogs("core.audit.services", level="ERROR") as captured:
                self.assertIsNone(log(AuditLog.Action.CREATE, instance=self.org))

        self.assertIn("Failed to write audit log entry", captured.output[0])


class AuditTrailFromAPITests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="trail-college")
        self.admin = user_with_permissions(
            self.org,
            ["campuses.view", "campuses.create", "campuses.update", "campuses.delete",
             "audit.view"],
            email="trail@test.edu",
        )
        self.authenticate(self.admin)

    def test_create_is_recorded_with_actor_and_request_context(self):
        self.client.post("/api/v1/campuses/", {"name": "New Campus", "code": "new"})

        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CREATE, object_type="organizations.Campus"
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_id, self.admin.pk)
        self.assertEqual(entry.actor_email, self.admin.email)
        self.assertEqual(entry.organization_id, self.org.pk)
        self.assertEqual(entry.request_method, "POST")
        self.assertEqual(entry.object_repr[-10:], "New Campus"[-10:])

    def test_update_records_before_and_after_values(self):
        campus = create_campus(self.org, code="upd", name="Before")
        self.client.patch(f"/api/v1/campuses/{campus.pk}/", {"name": "After"})

        entry = AuditLog.objects.filter(action=AuditLog.Action.UPDATE).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.changes["name"], {"before": "Before", "after": "After"})

    def test_a_no_op_update_writes_nothing(self):
        campus = create_campus(self.org, code="noop", name="Same")
        before = AuditLog.objects.filter(action=AuditLog.Action.UPDATE).count()

        self.client.patch(f"/api/v1/campuses/{campus.pk}/", {"name": "Same"})

        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.UPDATE).count(), before
        )

    def test_delete_is_recorded(self):
        campus = create_campus(self.org, code="del", name="Doomed")
        self.client.delete(f"/api/v1/campuses/{campus.pk}/")

        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.DELETE, object_id=str(campus.pk)
            ).exists()
        )


class AuditAPITests(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="auditapi-college")
        self.auditor = user_with_permissions(
            self.org, ["audit.view"], email="auditor@test.edu"
        )

    def test_audit_log_requires_the_audit_permission(self):
        plain = create_user(self.org, email="plain@test.edu")
        self.authenticate(plain)

        self.assertEqual(self.client.get("/api/v1/audit-logs/").status_code, 403)

    def test_audit_log_is_read_only(self):
        self.authenticate(self.auditor)

        self.assertEqual(self.client.get("/api/v1/audit-logs/").status_code, 200)
        response = self.client.post("/api/v1/audit-logs/", {"action": "create"})
        self.assertEqual(response.status_code, 405)

    def test_filter_by_action(self):
        self.authenticate(self.auditor)
        response = self.client.get("/api/v1/audit-logs/", {"action": "login"})

        self.assertEqual(response.status_code, 200)
        for row in response.data["results"]:
            self.assertEqual(row["action"], "login")
