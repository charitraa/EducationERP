"""Registering, approving, capacity, and withdrawing."""
from ..models import RegistrationMode
from .base import API, EventTestCase

EVENTS = f"{API}/events/"
REGISTRATIONS = f"{API}/event-registrations/"


class OpenRegistrationTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.event = self.make_event(registration_mode=RegistrationMode.OPEN)

    def test_registering_confirms_straight_away(self):
        from tests.factories import create_user

        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)
        r = self.client.post(f"{EVENTS}{self.event.pk}/register/", {"note": "Excited!"})
        self.assertEqual((r.status_code, r.data["status"]), (201, "confirmed"))

    def test_an_overlong_note_is_refused(self):
        from tests.factories import create_user

        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)
        r = self.client.post(f"{EVENTS}{self.event.pk}/register/", {"note": "x" * 256})
        self.assertEqual(r.status_code, 400, r.data)
        self.assertFalse(self.event.registrations.exists())

    def test_cannot_register_twice(self):
        from tests.factories import create_user

        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)
        self.client.post(f"{EVENTS}{self.event.pk}/register/")
        r = self.client.post(f"{EVENTS}{self.event.pk}/register/")
        self.assertError(r, 409, "already_registered")

    def test_only_a_student_can_register(self):
        self.login(self.office)
        r = self.client.post(f"{EVENTS}{self.event.pk}/register/")
        self.assertError(r, 403, "not_a_student")

    def test_capacity_closes_registration(self):
        from tests.factories import create_user

        self.event.capacity = 1
        self.event.save(update_fields=["capacity"])
        for student, email in ((self.ram, "ram@kmc.test"), (self.shyam, "shyam@kmc.test")):
            user = create_user(self.org, email=email)
            student.user = user
            student.save(update_fields=["user"])
        self.login(self.ram.user)
        self.assertEqual(self.client.post(f"{EVENTS}{self.event.pk}/register/").status_code, 201)
        self.login(self.shyam.user)
        self.assertError(self.client.post(f"{EVENTS}{self.event.pk}/register/"), 409, "event_full")

    def test_cannot_register_for_a_draft_or_cancelled_event(self):
        from tests.factories import create_user

        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)
        draft = self.make_event(name="Draft event", status="draft")
        self.assertError(self.client.post(f"{EVENTS}{draft.pk}/register/"), 409, "not_published")
        cancelled = self.make_event(name="Cancelled event", status="cancelled")
        self.assertError(self.client.post(f"{EVENTS}{cancelled.pk}/register/"), 409, "not_published")

    def test_a_student_can_withdraw(self):
        from tests.factories import create_user

        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)
        r = self.client.post(f"{EVENTS}{self.event.pk}/register/")
        reg_id = r.data["id"]
        r2 = self.client.post(f"{REGISTRATIONS}{reg_id}/withdraw/")
        self.assertEqual((r2.status_code, r2.data["status"]), (200, "withdrawn"))
        # Freed the slot: they can register again.
        self.assertEqual(self.client.post(f"{EVENTS}{self.event.pk}/register/").status_code, 201)

    def test_only_the_registrant_can_withdraw(self):
        from tests.factories import create_user

        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)
        r = self.client.post(f"{EVENTS}{self.event.pk}/register/")
        reg_id = r.data["id"]
        self.login(self.office)
        r2 = self.client.post(f"{REGISTRATIONS}{reg_id}/withdraw/")
        self.assertEqual(r2.status_code, 403)


class ApprovalRegistrationTests(EventTestCase):
    def setUp(self):
        super().setUp()
        self.event = self.make_event(registration_mode=RegistrationMode.APPROVAL)
        from tests.factories import create_user

        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])

    def register(self):
        self.login(self.ram.user)
        return self.client.post(f"{EVENTS}{self.event.pk}/register/")

    def test_starts_pending(self):
        r = self.register()
        self.assertEqual((r.status_code, r.data["status"]), (201, "pending"))

    def test_the_organizer_approves(self):
        r = self.register()
        reg_id = r.data["id"]
        self.login(self.hari_user)
        r2 = self.client.post(f"{REGISTRATIONS}{reg_id}/decide/", {"approve": True})
        self.assertEqual((r2.status_code, r2.data["status"]), (200, "confirmed"))

    def test_rejecting_needs_a_reason(self):
        r = self.register()
        reg_id = r.data["id"]
        self.login(self.office)
        self.assertEqual(self.client.post(f"{REGISTRATIONS}{reg_id}/decide/", {"approve": False}).status_code, 400)
        r2 = self.client.post(f"{REGISTRATIONS}{reg_id}/decide/", {"approve": False, "note": "Not eligible"})
        self.assertEqual((r2.status_code, r2.data["status"]), (200, "rejected"))

    def test_cannot_decide_twice(self):
        r = self.register()
        reg_id = r.data["id"]
        self.login(self.office)
        self.client.post(f"{REGISTRATIONS}{reg_id}/decide/", {"approve": True})
        r2 = self.client.post(f"{REGISTRATIONS}{reg_id}/decide/", {"approve": True})
        self.assertError(r2, 409, "already_decided")

    def test_someone_else_organizing_a_different_event_cannot_decide(self):
        r = self.register()
        reg_id = r.data["id"]
        from tests.factories import create_staff_member, user_with_system_role

        other_staff = create_staff_member(self.campus, employee_number="E-9", first_name="Other")
        other_user = user_with_system_role(self.org, "staff", email="other@kmc.test")
        other_staff.user = other_user
        other_staff.save(update_fields=["user"])
        self.login(other_user)
        r2 = self.client.post(f"{REGISTRATIONS}{reg_id}/decide/", {"approve": True})
        self.assertError(r2, 403, "not_your_event")

    def test_approving_checks_capacity_too(self):
        self.event.capacity = 0
        self.event.save(update_fields=["capacity"])
        r = self.register()
        reg_id = r.data["id"]
        self.login(self.office)
        r2 = self.client.post(f"{REGISTRATIONS}{reg_id}/decide/", {"approve": True})
        self.assertError(r2, 409, "event_full")


class StaffRegistrationTests(EventTestCase):
    """The organizer or the office enters students (a team sheet, students
    with no account) — through the same rules as self-registration."""

    def setUp(self):
        super().setUp()
        self.event = self.make_event(registration_mode=RegistrationMode.APPROVAL, capacity=2)

    def enter(self, student, who=None, event=None, **extra):
        self.login(who or self.hari_user)
        return self.client.post(REGISTRATIONS, {"event": (event or self.event).pk, "student": student.pk, **extra})

    def test_the_organizer_enters_a_student_confirmed(self):
        r = self.enter(self.ram, note="Goalkeeper")
        self.assertEqual((r.status_code, r.data["status"], r.data["note"]), (201, "confirmed", "Goalkeeper"))

    def test_the_office_can_too(self):
        r = self.enter(self.ram, who=self.office)
        self.assertEqual(r.status_code, 201, r.data)

    def test_capacity_and_duplicates_still_apply(self):
        self.enter(self.ram)
        self.assertError(self.enter(self.ram), 409, "already_registered")
        self.enter(self.shyam)
        self.assertError(self.enter(self.gita), 409, "event_full")
        self.assertEqual(self.event.registrations.count(), 2)

    def test_closed_registration_still_applies(self):
        event = self.make_event(name="Past", registration_mode=RegistrationMode.NONE)
        self.assertError(self.enter(self.ram, event=event), 409, "registration_closed")

    def test_only_the_events_own_campus(self):
        from tests.factories import create_student

        elsewhere = create_student(self.other_campus, student_number="S-9", first_name="Bina")
        self.assertError(self.enter(elsewhere, who=self.principal), 400, "wrong_campus")

    def test_someone_else_organizing_a_different_event_cannot(self):
        from tests.factories import create_staff_member, user_with_system_role

        other_staff = create_staff_member(self.campus, employee_number="E-9", first_name="Other")
        other_user = user_with_system_role(self.org, "staff", email="other@kmc.test")
        other_staff.user = other_user
        other_staff.save(update_fields=["user"])
        self.assertError(self.enter(self.ram, who=other_user), 403, "not_your_event")
        self.assertFalse(self.event.registrations.exists())

    def test_a_student_cannot_enter_others(self):
        from tests.factories import create_user

        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.assertEqual(self.enter(self.shyam, who=user).status_code, 403)


class NoRegistrationTests(EventTestCase):
    def test_registration_is_closed_for_a_none_mode_event(self):
        from tests.factories import create_user

        event = self.make_event(registration_mode=RegistrationMode.NONE)
        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)
        self.assertError(self.client.post(f"{EVENTS}{event.pk}/register/"), 409, "registration_closed")
