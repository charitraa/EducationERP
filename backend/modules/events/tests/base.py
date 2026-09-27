"""A campus running a sports day. Ram and Shyam are students; Hari is the
staff member organizing the event."""
from datetime import date, timedelta

from django.utils import timezone

from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_staff_member,
    create_student,
    user_with_system_role,
)

from modules.events.models import Event, EventCategory, EventStatus, RegistrationMode

TODAY = date.today()
NOW = timezone.now()
API = "/api/v1"
_UNSET = object()  # distinct from an explicit campus=None (an org-wide, shared event)


class EventTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="lalitpur", name="Lalitpur")
        self.other_campus = create_campus(self.org, code="bhaktapur", name="Bhaktapur")

        self.ram = create_student(self.campus, student_number="S-1", first_name="Ram",
                                  admitted_on=TODAY - timedelta(days=120))
        self.shyam = create_student(self.campus, student_number="S-2", first_name="Shyam",
                                    admitted_on=TODAY - timedelta(days=120))
        self.gita = create_student(self.campus, student_number="S-3", first_name="Gita",
                                   admitted_on=TODAY - timedelta(days=120))

        self.hari = create_staff_member(self.campus, employee_number="E-1", first_name="Hari")
        hari_user = user_with_system_role(self.org, "staff", email="hari@kmc.test")
        self.hari.user = hari_user
        self.hari.save(update_fields=["user"])
        self.hari_user = hari_user

        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test", campus=self.campus)
        self.principal = user_with_system_role(self.org, "org-admin", email="principal@kmc.test")

        self.category = EventCategory.objects.create(organization=self.org, code="sports", name="Sports")

    def login(self, who):
        self.logout()
        self.authenticate(getattr(who, "user", None) or who)

    def make_event(self, name="Sports Day", organized_by=None, status=EventStatus.PUBLISHED,
                  registration_mode=RegistrationMode.OPEN, campus=_UNSET, category=None, **fields):
        event = Event.objects.create(
            organization=self.org, campus=self.campus if campus is _UNSET else campus,
            category=self.category if category is None else category,
            name=name, start_at=NOW + timedelta(days=7), end_at=NOW + timedelta(days=7, hours=3),
            status=EventStatus.DRAFT, registration_mode=registration_mode,
            organized_by=organized_by if organized_by is not None else self.hari, **fields,
        )
        if status == EventStatus.PUBLISHED:
            from modules.events import services

            services.publish_event(event)
        elif status == EventStatus.CANCELLED:
            from modules.events import services

            services.publish_event(event)
            services.cancel_event(event, "Rescheduling")
        return event

    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.data)
        self.assertEqual(response.data["error"]["code"], code, response.data)
