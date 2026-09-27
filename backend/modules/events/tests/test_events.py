"""Categories, events, and their draft → published → cancelled lifecycle."""
from datetime import timedelta

from django.utils import timezone

from ..models import Event, EventStatus
from .base import API, EventTestCase, NOW

CATEGORIES = f"{API}/event-categories/"
EVENTS = f"{API}/events/"


class CategoryTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def test_crud_and_lowercase_code(self):
        r = self.client.post(CATEGORIES, {"code": "Cultural", "name": "Cultural"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["code"], "cultural")
        self.assertEqual(self.client.post(CATEGORIES, {"code": "cultural", "name": "Dup"}).status_code, 400)
        self.assertEqual(self.client.patch(f"{CATEGORIES}{r.data['id']}/", {"description": "x"}).status_code, 200)
        self.assertEqual(self.client.delete(f"{CATEGORIES}{r.data['id']}/").status_code, 204)

    def test_cannot_delete_a_category_in_use(self):
        self.make_event()
        self.assertError(self.client.delete(f"{CATEGORIES}{self.category.pk}/"), 409, "in_use")

    def test_a_teacher_cannot_manage_categories(self):
        self.login(self.hari_user)
        self.assertEqual(self.client.get(CATEGORIES).status_code, 200)
        self.assertEqual(self.client.post(CATEGORIES, {"code": "x", "name": "X"}).status_code, 403)


class CreateEventTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def body(self, **extra):
        return {"campus": self.campus.pk, "category": self.category.pk, "name": "Debate", "venue": "Hall",
                "start_at": (NOW + timedelta(days=10)).isoformat(), "end_at": (NOW + timedelta(days=10, hours=2)).isoformat(),
                "registration_mode": "open", **extra}

    def test_create_is_a_draft(self):
        r = self.client.post(EVENTS, self.body())
        self.assertEqual((r.status_code, r.data["status"]), (201, "draft"))

    def test_end_must_not_be_before_start(self):
        r = self.client.post(EVENTS, self.body(end_at=(NOW + timedelta(days=9)).isoformat()))
        self.assertEqual(r.status_code, 400)

    def test_an_event_can_have_no_campus(self):
        self.login(self.principal)  # organization-wide, unlike self.office (campus-scoped)
        r = self.client.post(EVENTS, self.body(campus=None))
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIsNone(r.data["campus"])

    def test_only_an_organization_wide_role_can_make_a_shared_event(self):
        self.login(self.office)  # campus-admin, scoped to lalitpur
        r = self.client.post(EVENTS, self.body(campus=None))
        self.assertEqual(r.status_code, 403)
        self.login(self.principal)
        self.assertEqual(self.client.post(EVENTS, self.body(campus=None)).status_code, 201)

    def test_a_teacher_cannot_create_events(self):
        self.login(self.hari_user)
        self.assertEqual(self.client.post(EVENTS, self.body()).status_code, 403)


class LifecycleTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)
        self.event = self.make_event(status=EventStatus.DRAFT)

    def test_publish_then_cancel(self):
        r = self.client.post(f"{EVENTS}{self.event.pk}/publish/")
        self.assertEqual((r.status_code, r.data["status"]), (200, "published"))
        self.assertEqual(self.client.post(f"{EVENTS}{self.event.pk}/cancel/", {}).status_code, 400)
        r = self.client.post(f"{EVENTS}{self.event.pk}/cancel/", {"reason": "Bad weather"})
        self.assertEqual((r.status_code, r.data["status"]), (200, "cancelled"))

    def test_cannot_publish_twice(self):
        self.client.post(f"{EVENTS}{self.event.pk}/publish/")
        self.assertError(self.client.post(f"{EVENTS}{self.event.pk}/publish/"), 409, "not_draft")

    def test_cannot_cancel_twice(self):
        self.client.post(f"{EVENTS}{self.event.pk}/publish/")
        self.client.post(f"{EVENTS}{self.event.pk}/cancel/", {"reason": "x"})
        self.assertError(self.client.post(f"{EVENTS}{self.event.pk}/cancel/", {"reason": "y"}), 409,
                         "already_cancelled")

    def test_a_cancelled_event_cannot_be_edited(self):
        self.client.post(f"{EVENTS}{self.event.pk}/publish/")
        self.client.post(f"{EVENTS}{self.event.pk}/cancel/", {"reason": "x"})
        r = self.client.patch(f"{EVENTS}{self.event.pk}/", {"name": "New"})
        self.assertEqual(r.status_code, 400)
