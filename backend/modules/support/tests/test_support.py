from tests.base import APITestCaseBase
from tests.factories import create_campus, create_organization, create_student, create_user, user_with_system_role

API = "/api/v1"


class SupportTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test", campus=self.campus)
        self.ram_user = create_user(self.org, email="ram@kmc.test", user_type="student")
        self.ram = create_student(self.campus, student_number="S-1", first_name="Ram", user=self.ram_user)
        self.other_user = create_user(self.org, email="other@kmc.test", user_type="student")

    def login(self, who):
        self.logout()
        self.authenticate(who)

    def raise_ticket(self):
        self.login(self.ram_user)
        return self.client.post(f"{API}/support/tickets/", {"subject": "Can't log in", "description": "Help"})


class TicketTests(SupportTestCase):
    def test_any_user_can_raise_a_ticket(self):
        r = self.raise_ticket()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "open")
        self.assertEqual(r.data["campus"], self.campus.pk)

    def test_the_raiser_sees_their_own_ticket(self):
        pk = self.raise_ticket().data["id"]
        r = self.client.get(f"{API}/support/tickets/{pk}/")
        self.assertEqual(r.status_code, 200)

    def test_another_user_cannot_see_someone_elses_ticket(self):
        pk = self.raise_ticket().data["id"]
        self.login(self.other_user)
        self.assertEqual(self.client.get(f"{API}/support/tickets/{pk}/").status_code, 404)

    def test_the_office_sees_every_ticket_in_their_campus(self):
        self.raise_ticket()
        self.login(self.office)
        self.assertEqual(self.client.get(f"{API}/support/tickets/").data["count"], 1)

    def test_only_the_office_can_assign(self):
        pk = self.raise_ticket().data["id"]
        self.login(self.other_user)
        r = self.client.post(f"{API}/support/tickets/{pk}/assign/", {"assigned_to": self.office.pk})
        self.assertEqual(r.status_code, 403)

    def test_the_office_assigns_and_it_moves_to_in_progress(self):
        pk = self.raise_ticket().data["id"]
        self.login(self.office)
        r = self.client.post(f"{API}/support/tickets/{pk}/assign/", {"assigned_to": self.office.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "in_progress")

    def test_the_assignee_can_resolve(self):
        pk = self.raise_ticket().data["id"]
        self.login(self.office)
        self.client.post(f"{API}/support/tickets/{pk}/assign/", {"assigned_to": self.office.pk})
        r = self.client.post(f"{API}/support/tickets/{pk}/resolve/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "resolved")

    def test_a_stranger_cannot_resolve(self):
        pk = self.raise_ticket().data["id"]
        self.login(self.other_user)
        # Not in their queryset at all (not raiser, assignee, or a manage-holder): a 404.
        self.assertEqual(self.client.post(f"{API}/support/tickets/{pk}/resolve/").status_code, 404)

    def test_the_raiser_can_close_their_own_resolved_ticket(self):
        pk = self.raise_ticket().data["id"]
        self.login(self.office)
        self.client.post(f"{API}/support/tickets/{pk}/assign/", {"assigned_to": self.office.pk})
        self.client.post(f"{API}/support/tickets/{pk}/resolve/")
        self.login(self.ram_user)
        r = self.client.post(f"{API}/support/tickets/{pk}/close/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["status"], "closed")

    def test_a_bare_patch_is_not_allowed(self):
        pk = self.raise_ticket().data["id"]
        self.login(self.ram_user)
        r = self.client.patch(f"{API}/support/tickets/{pk}/", {"status": "resolved"})
        self.assertEqual(r.status_code, 405)

    def test_comment_thread(self):
        pk = self.raise_ticket().data["id"]
        r = self.client.post(f"{API}/support/tickets/{pk}/comments/", {"body": "Any update?"})
        self.assertEqual(r.status_code, 201, r.data)
        r = self.client.get(f"{API}/support/tickets/{pk}/comments/")
        self.assertEqual(len(r.data), 1)

    def test_needs_a_login(self):
        self.assertEqual(self.client.get(f"{API}/support/tickets/").status_code, 401)
