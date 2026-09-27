"""Who can see and do what: roles, campuses, organizations."""
from tests.factories import create_campus, create_organization, user_with_system_role

from ..models import Award
from .base import API, EventTestCase

STAFF_ENDPOINTS = ["event-categories", "events", "event-registrations", "point-rules", "point-entries",
                   "student-points", "awards", "award-rules", "student-awards"]


class AnonymousTests(EventTestCase):
    def test_every_endpoint_needs_a_login(self):
        for name in STAFF_ENDPOINTS + ["events/me"]:
            self.assertEqual(self.client.get(f"{API}/{name}/").status_code, 401, name)


class CampusScopeTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.event = self.make_event()
        self.elsewhere = user_with_system_role(self.org, "campus-admin", email="other@kmc.test",
                                               campus=self.other_campus)

    def test_a_campus_scoped_role_does_not_see_another_campuss_event(self):
        self.login(self.elsewhere)
        self.assertEqual(self.client.get(f"{API}/events/").data["count"], 0)
        self.assertEqual(self.client.get(f"{API}/events/{self.event.pk}/").status_code, 404)

    def test_a_campus_scoped_role_still_sees_a_shared_event(self):
        self.login(self.principal)
        shared = self.make_event(name="Assembly", campus=None)
        self.login(self.elsewhere)
        r = self.client.get(f"{API}/events/")
        self.assertEqual([e["id"] for e in r.data["results"]], [shared.pk])

    def test_an_organization_wide_admin_sees_every_campus(self):
        self.login(self.principal)
        self.assertEqual(self.client.get(f"{API}/events/").data["count"], 1)


class TenantTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.event = self.make_event()
        self.rival_org = create_organization(code="rival")
        create_campus(self.rival_org, code="main")
        self.rival = user_with_system_role(self.rival_org, "org-admin", email="rival@rival.test")

    def test_nothing_is_visible(self):
        self.login(self.rival)
        for name in STAFF_ENDPOINTS:
            r = self.client.get(f"{API}/{name}/")
            self.assertEqual((r.status_code, r.data["count"]), (200, 0), name)
        self.assertEqual(self.client.get(f"{API}/events/{self.event.pk}/").status_code, 404)

    def test_cannot_use_our_ids(self):
        self.login(self.rival)
        rival_campus = self.rival_org.campuses.first()
        body = {"campus": rival_campus.pk, "category": self.category.pk, "name": "Steal",
                "start_at": "2027-01-01T10:00:00Z", "end_at": "2027-01-01T12:00:00Z"}
        r = self.client.post(f"{API}/events/", body)
        self.assertEqual(r.status_code, 400)

    def test_award_rules_cannot_reference_our_category(self):
        self.login(self.rival)
        award = Award.objects.create(organization=self.rival_org, kind="badge", code="x", name="X")
        r = self.client.post(f"{API}/award-rules/", {"award": award.pk, "threshold_kind": "events_attended",
                                                      "threshold_value": 1, "category": self.category.pk})
        self.assertEqual(r.status_code, 400)


class RoleTests(EventTestCase):
    def test_view_only_role_cannot_write(self):
        from tests.factories import user_with_permissions

        viewer = user_with_permissions(self.org, ["events.view"], email="viewer@kmc.test")
        self.login(viewer)
        self.assertEqual(self.client.get(f"{API}/events/").status_code, 200)
        self.assertEqual(self.client.post(f"{API}/event-categories/", {"code": "x", "name": "X"}).status_code, 403)

    def test_coordinate_only_role_cannot_manage_categories(self):
        from tests.factories import user_with_permissions

        coordinator = user_with_permissions(self.org, ["events.coordinate", "events.view"],
                                            email="coord@kmc.test")
        self.login(coordinator)
        self.assertEqual(self.client.post(f"{API}/event-categories/", {"code": "x", "name": "X"}).status_code, 403)
