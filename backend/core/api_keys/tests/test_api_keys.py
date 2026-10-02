from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from core.accounts.models import User
from core.api_keys.models import ApiKey
from core.audit.models import AuditLog
from core.permissions.selectors import users_holding
from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_role,
    create_student,
    user_with_permissions,
    user_with_system_role,
)

API = "/api/v1"


class ApiKeyTestCase(APITestCaseBase):
    def setUp(self):
        cache.clear()  # throttle counters
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.branch = create_campus(self.org, code="branch")
        self.admin = user_with_system_role(self.org, "org-admin", email="admin@kmc.test")
        self.reader = create_role(self.org, code="student-reader", permissions=["students.view"])
        self.writer = create_role(self.org, code="student-writer", permissions=["students.view", "students.create"])
        self.ram = create_student(self.campus, student_number="S-1", first_name="Ram")
        self.sita = create_student(self.branch, student_number="S-2", first_name="Sita")

    def make_key(self, **body):
        self.authenticate(self.admin)
        response = self.client.post(f"{API}/api-keys/", {"name": "Website", **body}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.client.credentials()
        return response.data

    def with_key(self, raw, method="get", url="students/", body=None, **headers):
        self.client.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw}", **headers)
        return getattr(self.client, method)(f"{API}/{url}", body or {}, format="json")


class AccessTests(ApiKeyTestCase):
    def test_a_key_acts_with_its_roles_and_campus(self):
        created = self.make_key(grants=[{"role": self.reader.pk, "campus": self.campus.pk}])
        raw = created["key"]
        self.assertTrue(raw.startswith(f"erp_{created['prefix']}_"))
        self.assertEqual(created["roles"], [{"role": self.reader.pk, "role_code": "student-reader",
                                             "campus": self.campus.pk}])
        # The secret is never shown again.
        self.authenticate(self.admin)
        self.assertNotIn("key", self.client.get(f"{API}/api-keys/{created['id']}/").data)

        response = self.with_key(raw)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([s["first_name"] for s in response.data["results"]], ["Ram"])  # its campus only
        self.assertEqual(self.with_key(raw, url=f"students/{self.sita.pk}/").status_code, 404)
        self.assertEqual(self.with_key(raw, "post", body={"first_name": "X"}).status_code, 403)  # no create
        key = ApiKey.objects.get(pk=created["id"])
        self.assertIsNotNone(key.last_used_at)
        self.assertEqual(key.last_used_ip, "127.0.0.1")
        # The other header works too.
        self.client.credentials(HTTP_X_API_KEY=raw)
        self.assertEqual(self.client.get(f"{API}/students/").status_code, 200)

    def test_writes_are_audited_as_the_key(self):
        raw = self.make_key(name="Admissions site", grants=[{"role": self.writer.pk}])["key"]
        response = self.with_key(raw, "post", body={"student_number": "S-9", "first_name": "Gita",
                                                    "last_name": "Rai", "campus": self.campus.pk})
        self.assertEqual(response.status_code, 201, response.data)
        entry = AuditLog.objects.filter(action="create", object_id=str(response.data["id"])).first()
        self.assertEqual(entry.actor.first_name, "Admissions site")

    def test_read_only(self):
        raw = self.make_key(read_only=True, grants=[{"role": self.writer.pk}])["key"]
        self.assertEqual(self.with_key(raw).status_code, 200)
        response = self.with_key(raw, "post", body={"student_number": "S-9", "first_name": "G", "last_name": "R",
                                                    "campus": self.campus.pk})
        self.assertEqual((response.status_code, response.data["error"]["code"]), (403, "read_only_key"))

    def test_refused_keys(self):
        created = self.make_key(grants=[{"role": self.reader.pk}])
        raw = created["key"]
        for bad in ("nonsense", f"erp_{created['prefix']}_wrong", "erp_zzzzzzzzzz_" + raw.split("_", 2)[2]):
            with self.subTest(key=bad):
                self.assertEqual(self.with_key(bad).status_code, 401)
        ApiKey.objects.filter(pk=created["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.with_key(raw).status_code, 401)
        ApiKey.objects.filter(pk=created["id"]).update(expires_at=None)
        self.assertEqual(self.with_key(raw).status_code, 200)

        self.authenticate(self.admin)
        self.assertEqual(self.client.post(f"{API}/api-keys/{created['id']}/revoke/", {"reason": "Leaked"})
                         .data["is_usable"], False)
        self.assertEqual(self.with_key(raw).status_code, 401)
        self.authenticate(self.admin)
        self.assertEqual(self.client.post(f"{API}/api-keys/{created['id']}/rotate/").status_code, 409)

    def test_rotate(self):
        created = self.make_key(grants=[{"role": self.reader.pk}])
        self.authenticate(self.admin)
        new = self.client.post(f"{API}/api-keys/{created['id']}/rotate/").data["key"]
        self.assertNotEqual(new, created["key"])
        self.assertEqual(self.with_key(created["key"]).status_code, 401)
        self.assertEqual(self.with_key(new).status_code, 200)

    def test_address_allowlist(self):
        created = self.make_key(allowed_ips=["10.0.0.0/8"], grants=[{"role": self.reader.pk}])
        response = self.with_key(created["key"])
        self.assertEqual((response.status_code, response.data["error"]["code"]), (403, "ip_not_allowed"))
        self.authenticate(self.admin)
        self.client.patch(f"{API}/api-keys/{created['id']}/", {"allowed_ips": ["10.0.0.0/8", "127.0.0.1"]},
                          format="json")
        self.assertEqual(self.with_key(created["key"]).status_code, 200)
        self.authenticate(self.admin)
        self.assertEqual(self.client.patch(f"{API}/api-keys/{created['id']}/", {"allowed_ips": ["nope"]},
                                           format="json").status_code, 400)

    def test_own_rate_limit(self):
        raw = self.make_key(rate_limit="2/min", grants=[{"role": self.reader.pk}])["key"]
        self.assertEqual([self.with_key(raw).status_code for _ in range(3)], [200, 200, 429])
        self.authenticate(self.admin)
        self.assertEqual(self.client.post(f"{API}/api-keys/", {"name": "x", "rate_limit": "fast"},
                                          format="json").status_code, 400)

    def test_another_organizations_data_is_out_of_reach(self):
        other = create_organization(code="other")
        theirs = create_student(create_campus(other), student_number="X-1")
        raw = self.make_key(grants=[{"role": self.reader.pk}])["key"]
        self.assertEqual(self.with_key(raw, url=f"students/{theirs.pk}/").status_code, 404)


class ManagementTests(ApiKeyTestCase):
    def test_integration_users_stay_out_of_the_way(self):
        created = self.make_key(grants=[{"role": self.reader.pk}])
        key_user = ApiKey.objects.get(pk=created["id"]).user
        self.assertEqual(key_user.user_type, User.Type.INTEGRATION)
        self.assertFalse(key_user.has_usable_password())
        self.authenticate(self.admin)
        self.assertNotIn(key_user.pk, [u["id"] for u in self.client.get(f"{API}/users/").data["results"]])
        self.assertEqual(self.client.get(f"{API}/users/{key_user.pk}/").status_code, 404)
        self.assertEqual(self.client.post(f"{API}/users/{key_user.pk}/set-password/",
                                          {"password": "Whatever-12345"}).status_code, 404)
        self.client.credentials()
        self.assertEqual(self.client.post(f"{API}/auth/login/", {"email": key_user.email, "password": ""})
                         .status_code, 400)
        # Nobody notifies a key.
        self.assertNotIn(key_user, users_holding(["students.view"], campus_id=self.campus.pk,
                                                 organization_id=self.org.pk))

    def test_no_granting_more_than_you_hold(self):
        manager = user_with_permissions(self.org, ["api_keys.manage", "students.view"], email="m@kmc.test")
        self.authenticate(manager)
        response = self.client.post(f"{API}/api-keys/", {"name": "x", "grants": [{"role": self.writer.pk}]},
                                    format="json")
        self.assertEqual(response.data["error"]["code"], "role_exceeds_own_permissions")
        self.assertFalse(ApiKey.objects.exists())  # nothing half-made
        pk = self.client.post(f"{API}/api-keys/", {"name": "x"}, format="json").data["id"]
        self.assertEqual(self.client.post(f"{API}/api-keys/{pk}/assign-role/", {"role": self.writer.pk})
                         .status_code, 403)
        self.assertEqual(self.client.post(f"{API}/api-keys/{pk}/assign-role/", {"role": self.reader.pk})
                         .status_code, 201)
        self.assertEqual(self.client.post(f"{API}/api-keys/{pk}/revoke-role/", {"role": self.reader.pk})
                         .status_code, 204)

    def test_keys_cannot_manage_keys(self):
        boss = create_role(self.org, code="key-boss", permissions=["api_keys.manage", "students.view"])
        raw = self.make_key(grants=[{"role": boss.pk}])["key"]
        self.assertEqual(self.with_key(raw, url="api-keys/").status_code, 403)
        self.assertEqual(self.with_key(raw, "post", url="api-keys/", body={"name": "child"}).status_code, 403)

    def test_campus_admins_do_not_manage_keys(self):
        campus_admin = user_with_system_role(self.org, "campus-admin", email="ca@kmc.test", campus=self.campus)
        self.authenticate(campus_admin)
        self.assertEqual(self.client.get(f"{API}/api-keys/").status_code, 403)

    def test_foreign_roles_and_campuses_refused(self):
        other = create_organization(code="other")
        their_role = create_role(other, code="theirs", permissions=["students.view"])
        self.authenticate(self.admin)
        for grant in ({"role": their_role.pk}, {"role": self.reader.pk, "campus": create_campus(other).pk}):
            with self.subTest(grant=grant):
                self.assertEqual(self.client.post(f"{API}/api-keys/", {"name": "x", "grants": [grant]},
                                                  format="json").status_code, 400)
        self.assertEqual(self.client.post(f"{API}/api-keys/", {"name": "x", "expires_at": "2000-01-01T00:00:00Z"},
                                          format="json").status_code, 400)
        self.assertIn(self.client.delete(f"{API}/api-keys/1/").status_code, (403, 405))  # revoke, never delete
