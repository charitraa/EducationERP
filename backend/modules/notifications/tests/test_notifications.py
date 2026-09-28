"""Notifications are self-scoped: a user only ever sees their own."""
from tests.base import APITestCaseBase
from tests.factories import create_campus, create_organization, create_user

from ..models import Notification
from ..services import mark_all_read, mark_read, notify

API = "/api/v1"


class NotificationTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.alice = create_user(self.org, email="alice@kmc.test")
        self.bob = create_user(self.org, email="bob@kmc.test")

    def login(self, user):
        self.logout()
        self.authenticate(user)


class ServiceTests(NotificationTestCase):
    def test_notify_creates_one_row_per_recipient_and_ignores_duplicates_and_none(self):
        rows = notify([self.alice, self.bob, self.alice, None], event_type="test.event", title="Hi")
        self.assertEqual({r.recipient_id for r in rows}, {self.alice.pk, self.bob.pk})
        self.assertEqual(Notification.objects.count(), 2)

    def test_mark_read_is_idempotent(self):
        row = notify([self.alice], event_type="test.event", title="Hi")[0]
        mark_read(row)
        first_read_at = row.read_at
        mark_read(row)
        self.assertEqual(row.read_at, first_read_at)

    def test_mark_all_read(self):
        notify([self.alice, self.alice], event_type="a", title="1")  # one row: duplicates are ignored
        notify([self.alice], event_type="b", title="2")
        count = mark_all_read(self.alice)
        self.assertEqual(count, 2)


class ApiTests(NotificationTestCase):
    def test_a_user_only_sees_their_own_notifications(self):
        notify([self.alice], event_type="test.event", title="For Alice")
        notify([self.bob], event_type="test.event", title="For Bob")
        self.login(self.alice)
        r = self.client.get(f"{API}/notifications/")
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["title"], "For Alice")

    def test_cannot_retrieve_someone_elses_notification(self):
        row = notify([self.bob], event_type="test.event", title="For Bob")[0]
        self.login(self.alice)
        self.assertEqual(self.client.get(f"{API}/notifications/{row.pk}/").status_code, 404)

    def test_mark_read_action(self):
        row = notify([self.alice], event_type="test.event", title="Hi")[0]
        self.login(self.alice)
        r = self.client.post(f"{API}/notifications/{row.pk}/mark-read/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["is_read"])

    def test_unread_count(self):
        notify([self.alice, self.alice], event_type="a", title="1")
        self.login(self.alice)
        self.assertEqual(self.client.get(f"{API}/notifications/unread-count/").data["unread"], 1)

    def test_needs_a_login(self):
        self.assertEqual(self.client.get(f"{API}/notifications/").status_code, 401)
