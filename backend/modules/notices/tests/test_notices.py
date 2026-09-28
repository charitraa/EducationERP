from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_student,
    create_user,
    user_with_system_role,
)

from ..models import Notice
from ..services import publish_notice

API = "/api/v1"


class NoticeTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="lalitpur")
        self.other_campus = create_campus(self.org, code="bhaktapur")
        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test", campus=self.campus)
        self.ram_user = create_user(self.org, email="ram@kmc.test", user_type="student")
        self.ram = create_student(self.campus, student_number="S-1", first_name="Ram", user=self.ram_user)

    def login(self, who):
        self.logout()
        self.authenticate(getattr(who, "user", None) or who)

    def make_notice(self, published=True, **fields):
        notice = Notice.objects.create(organization=self.org, title="Notice", body="Body", **fields)
        if published:
            publish_notice(notice)
        return notice


class VisibilityTests(NoticeTestCase):
    def test_a_student_sees_a_published_campus_notice(self):
        self.make_notice(campus=self.campus, audience="students")
        self.login(self.ram)
        self.assertEqual(self.client.get(f"{API}/notices/").data["count"], 1)

    def test_a_student_does_not_see_another_campuss_notice(self):
        self.make_notice(campus=self.other_campus, audience="students")
        self.login(self.ram)
        self.assertEqual(self.client.get(f"{API}/notices/").data["count"], 0)

    def test_a_student_sees_an_org_wide_notice(self):
        self.make_notice(campus=None, audience="all")
        self.login(self.ram)
        self.assertEqual(self.client.get(f"{API}/notices/").data["count"], 1)

    def test_a_student_does_not_see_a_staff_only_notice(self):
        self.make_notice(campus=self.campus, audience="staff")
        self.login(self.ram)
        self.assertEqual(self.client.get(f"{API}/notices/").data["count"], 0)

    def test_a_draft_is_not_visible_to_a_student(self):
        self.make_notice(published=False, campus=self.campus, audience="all")
        self.login(self.ram)
        self.assertEqual(self.client.get(f"{API}/notices/").data["count"], 0)

    def test_the_office_sees_drafts_in_their_campus(self):
        self.make_notice(published=False, campus=self.campus, audience="all")
        self.login(self.office)
        self.assertEqual(self.client.get(f"{API}/notices/").data["count"], 1)


class PermissionTests(NoticeTestCase):
    def test_a_student_cannot_create_a_notice(self):
        self.login(self.ram)
        r = self.client.post(f"{API}/notices/", {"title": "X", "body": "Y", "audience": "all"})
        self.assertEqual(r.status_code, 403)

    def test_the_office_can_create_and_publish(self):
        self.login(self.office)
        r = self.client.post(f"{API}/notices/", {"title": "X", "body": "Y", "audience": "all",
                                                  "campus": self.campus.pk})
        self.assertEqual(r.status_code, 201, r.data)
        notice_id = r.data["id"]
        r = self.client.post(f"{API}/notices/{notice_id}/publish/")
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.data["published_at"])

    def test_publishing_twice_conflicts(self):
        notice = self.make_notice(campus=self.campus, audience="all")
        self.login(self.office)
        r = self.client.post(f"{API}/notices/{notice.pk}/publish/")
        self.assertEqual(r.status_code, 409)

    def test_needs_a_login(self):
        self.assertEqual(self.client.get(f"{API}/notices/").status_code, 401)
