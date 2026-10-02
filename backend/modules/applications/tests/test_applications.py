from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from modules.admissions.models import Admission
from modules.applications.models import Application, ApplicationType, ApprovalStep, Certificate
from modules.attendance.models import StaffAttendanceDay
from modules.events.models import Event, EventCategory
from modules.finance.models import Scholarship, StudentScholarship
from modules.hostel.models import Allocation, Bed, Building, Floor, HostelRoom, RoomType
from modules.hostel.services import allocate_hostel_room
from modules.hr.models import FiscalYear, LeaveRequest, LeaveType
from modules.notifications.models import Notification
from modules.parents.services import link_student
from modules.transport.models import Assignment, Route, Stop, Vehicle
from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_parent,
    create_role,
    create_staff_member,
    create_student,
    create_user,
    grant,
    user_with_permissions,
    user_with_system_role,
)

API = "/api/v1"


def next_weekday(after=None):
    day = (after or date.today()) + timedelta(days=1)
    while day.isoweekday() in (6, 7):
        day += timedelta(days=1)
    return day


class ApplicationsTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.branch = create_campus(self.org, code="branch")
        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test", campus=self.campus)
        self.branch_office = user_with_system_role(self.org, "campus-admin", email="branch@kmc.test",
                                                   campus=self.branch)
        self.admin = user_with_system_role(self.org, "org-admin", email="admin@kmc.test")

        self.ram_user = create_user(self.org, email="ram@kmc.test", user_type="student")
        self.ram = create_student(self.campus, student_number="S-1", first_name="Ram", gender="male",
                                  user=self.ram_user)
        self.parent_user = create_user(self.org, email="dad@kmc.test", user_type="parent")
        link_student(parent=create_parent(self.org, user=self.parent_user), student=self.ram,
                     relationship="father")
        self.shyam = create_student(self.campus, student_number="S-2", first_name="Shyam", gender="male")

        # A class teacher who decides the first step of student requests.
        self.teacher_user = create_user(self.org, email="teacher@kmc.test", user_type="staff")
        self.teacher = create_staff_member(self.campus, employee_number="E-1", user=self.teacher_user,
                                           joined_on=date(2020, 1, 1), gender="female")
        grant(self.teacher_user, create_role(self.org, code="class-teacher", permissions=["attendance.mark"]),
              campus=self.campus)

    def make_type(self, kind, steps, code=None, **fields):
        application_type = ApplicationType.objects.create(organization=self.org, code=code or kind, name=kind.title(),
                                                          kind=kind, **fields)
        for n, (name, permission) in enumerate(steps, 1):
            ApprovalStep.objects.create(organization=self.org, application_type=application_type, sequence=n,
                                        name=name, permission=permission)
        return application_type

    def submit(self, user, application_type, data, **extra):
        self.authenticate(user)
        return self.client.post(f"{API}/applications/", {"application_type": application_type.pk, "data": data,
                                                         **extra}, format="json")

    def act(self, user, pk, verb, **body):
        self.authenticate(user)
        return self.client.post(f"{API}/applications/{pk}/{verb}/", body, format="json")


class TypeSetupTests(ApplicationsTestCase):
    def test_create_type_with_steps_and_questions(self):
        self.authenticate(self.admin)
        response = self.client.post(f"{API}/application-types/", {
            "code": "Hostel-Bed", "name": "Hostel bed", "kind": "hostel",
            "fields": [{"name": "medical", "label": "Medical needs", "type": "text"},
                       {"name": "meal", "type": "choice", "choices": ["veg", "non-veg"], "required": True}],
            "steps": [{"name": "Class teacher", "permission": "attendance.mark"},
                      {"name": "Warden", "permission": "hostel.manage"}]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["code"], "hostel-bed")
        self.assertEqual([s["sequence"] for s in response.data["steps"]], [1, 2])
        self.assertEqual(response.data["decision_fields"], ["bed"])

    def test_step_rules(self):
        self.authenticate(self.admin)
        base = {"code": "h", "name": "H", "kind": "hostel"}
        for steps, why in (([], "none"), ([{"name": "x", "permission": "no.such"}], "unknown"),
                           ([{"name": "x", "permission": "attendance.mark"}], "last step must be hostel.manage")):
            response = self.client.post(f"{API}/application-types/", {**base, "steps": steps}, format="json")
            self.assertEqual(response.status_code, 400, why)
        response = self.client.post(f"{API}/application-types/", {
            "code": "g", "name": "G", "kind": "general", "is_public": True,
            "steps": [{"name": "Office", "permission": "applications.manage"}]}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f"{API}/application-types/", {
            "code": "g", "name": "G", "kind": "general", "fields": [{"name": "x", "type": "choice"}],
            "steps": [{"name": "Office", "permission": "applications.manage"}]}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_steps_locked_while_applications_are_open(self):
        general = self.make_type("general", [("Office", "applications.manage")])
        self.submit(self.ram_user, general, {"subject": "Bonafide letter"})
        self.authenticate(self.admin)
        response = self.client.patch(f"{API}/application-types/{general.pk}/", {
            "steps": [{"name": "Principal", "permission": "applications.view"}]}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.patch(f"{API}/application-types/{general.pk}/", {"name": "Renamed"},
                                           format="json").status_code, 200)
        self.assertEqual(self.client.delete(f"{API}/application-types/{general.pk}/").status_code, 409)

    def test_campus_office_sets_up_only_its_own_campus(self):
        self.authenticate(self.office)
        body = {"code": "c", "name": "C", "kind": "general",
                "steps": [{"name": "Office", "permission": "applications.manage"}]}
        self.assertEqual(self.client.post(f"{API}/application-types/", body, format="json").status_code, 403)
        body["campus"] = self.campus.pk
        self.assertEqual(self.client.post(f"{API}/application-types/", body, format="json").status_code, 201)
        self.authenticate(self.branch_office)
        response = self.client.post(f"{API}/application-types/", {**body, "code": "d"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_applicant_sees_available_forms_without_steps_permissions(self):
        self.make_type("general", [("Office", "applications.manage")])
        self.authenticate(self.ram_user)
        response = self.client.get(f"{API}/application-types/available/")
        self.assertEqual(response.data[0]["steps"], ["Office"])
        self.assertEqual(self.client.get(f"{API}/application-types/").status_code, 403)


class ChainTests(ApplicationsTestCase):
    def setUp(self):
        super().setUp()
        self.general = self.make_type("general", [("Class teacher", "attendance.mark"),
                                                  ("Office", "applications.manage")],
                                      fields=[{"name": "copies", "type": "number", "required": True,
                                               "label": "Copies", "choices": []}])

    def test_full_chain_with_send_back(self):
        response = self.submit(self.ram_user, self.general, {"subject": "Recommendation letter",
                                                             "extra": {"copies": 2}})
        self.assertEqual(response.status_code, 201, response.data)
        pk = response.data["id"]
        self.assertEqual((response.data["status"], response.data["step_name"]), ("in_review", "Class teacher"))
        self.assertTrue(Notification.objects.filter(recipient=self.teacher_user,
                                                    event_type="applications.awaiting_decision").exists())
        # A clerk who sees every application can't skip the teacher's step; the student can't decide their own.
        clerk = user_with_permissions(self.org, ["applications.view", "applications.manage"], email="c@kmc.test")
        response = self.act(clerk, pk, "approve")
        self.assertEqual((response.status_code, response.data["error"]["code"]), (403, "not_your_step"))
        self.assertEqual(self.act(self.ram_user, pk, "approve").status_code, 403)

        self.authenticate(self.teacher_user)
        self.assertEqual([a["id"] for a in self.client.get(f"{API}/applications/pending/").data["results"]], [pk])
        self.assertEqual(self.act(self.teacher_user, pk, "send-back", note="").status_code, 400)
        response = self.act(self.teacher_user, pk, "send-back", note="Say who it's addressed to")
        self.assertEqual(response.data["status"], "returned")
        self.assertEqual(self.act(self.teacher_user, pk, "approve").status_code, 409)

        bad = self.act(self.ram_user, pk, "resubmit", data={"subject": "x", "extra": {"copies": "two"}})
        self.assertEqual(bad.status_code, 400)
        response = self.act(self.ram_user, pk, "resubmit", data={"subject": "Recommendation for TU", "extra": {
            "copies": 1}}, note="Addressed to TU")
        self.assertEqual(response.data["status"], "in_review")

        response = self.act(self.teacher_user, pk, "approve", note="Good student")
        self.assertEqual((response.data["status"], response.data["step_name"]), ("in_review", "Office"))
        # The teacher still sees what they decided.
        self.authenticate(self.teacher_user)
        self.assertEqual(self.client.get(f"{API}/applications/{pk}/").status_code, 200)
        response = self.act(self.office, pk, "approve")
        self.assertEqual(response.data["status"], "approved")
        self.assertEqual([e["action"] for e in response.data["events"]],
                         ["submitted", "returned", "resubmitted", "approved", "completed"])
        self.assertTrue(Notification.objects.filter(recipient=self.ram_user, event_type="applications.approved")
                        .exists())

    def test_reject_and_withdraw(self):
        pk = self.submit(self.ram_user, self.general, {"subject": "x", "extra": {"copies": 1}}).data["id"]
        self.assertEqual(self.act(self.teacher_user, pk, "reject", note="").status_code, 400)
        self.assertEqual(self.act(self.teacher_user, pk, "reject", note="Not needed").data["status"], "rejected")
        self.assertEqual(self.act(self.ram_user, pk, "withdraw").status_code, 409)
        pk = self.submit(self.ram_user, self.general, {"subject": "y", "extra": {"copies": 1}}).data["id"]
        self.assertEqual(self.act(self.teacher_user, pk, "withdraw").status_code, 403)
        self.assertEqual(self.act(self.ram_user, pk, "withdraw").data["status"], "withdrawn")

    def test_required_extra_question(self):
        response = self.submit(self.ram_user, self.general, {"subject": "x"})
        self.assertEqual(response.status_code, 400)
        response = self.submit(self.ram_user, self.general, {"subject": "x", "extra": {"copies": 1, "bogus": 2}})
        self.assertEqual(response.status_code, 400)

    def test_who_sees_what(self):
        pk = self.submit(self.ram_user, self.general, {"subject": "x", "extra": {"copies": 1}}).data["id"]
        stranger = create_user(self.org, email="x@kmc.test", user_type="staff")
        self.authenticate(stranger)
        self.assertEqual(self.client.get(f"{API}/applications/{pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"{API}/applications/pending/").data["count"], 0)
        self.authenticate(self.parent_user)
        self.assertEqual(self.client.get(f"{API}/applications/{pk}/").status_code, 200)
        self.assertEqual([a["id"] for a in self.client.get(f"{API}/applications/me/").data["results"]], [pk])
        self.authenticate(self.branch_office)
        self.assertEqual(self.client.get(f"{API}/applications/").data["count"], 0)
        self.assertEqual(self.client.get(f"{API}/applications/{pk}/").status_code, 404)
        self.authenticate(self.office)
        self.assertEqual(self.client.get(f"{API}/applications/").data["count"], 1)

    def test_parent_applies_only_for_own_child(self):
        response = self.submit(self.parent_user, self.general, {"subject": "x", "extra": {"copies": 1}},
                               student=self.ram.pk)
        self.assertEqual(response.status_code, 201, response.data)
        response = self.submit(self.parent_user, self.general, {"subject": "x", "extra": {"copies": 1}},
                               student=self.shyam.pk)
        self.assertEqual(response.status_code, 403)

    def test_another_organizations_type_is_unknown(self):
        other = create_organization(code="other")
        theirs = ApplicationType.objects.create(organization=other, code="g", name="G", kind="general")
        response = self.submit(self.ram_user, theirs, {"subject": "x"})
        self.assertEqual(response.status_code, 400)


class KindTests(ApplicationsTestCase):
    def test_hostel_needs_a_bed_and_a_taken_bed_refuses_approval(self):
        hostel = self.make_type("hostel", [("Warden", "hostel.manage")])
        building = Building.objects.create(organization=self.org, campus=self.campus, code="b", name="Boys",
                                           gender="male")
        floor = Floor.objects.create(organization=self.org, building=building, number=0)
        rt = RoomType.objects.create(organization=self.org, code="d", name="Double")
        room = HostelRoom.objects.create(organization=self.org, building=building, floor=floor, number="1",
                                         room_type=rt)
        bed = Bed.objects.create(organization=self.org, room=room, label="A")
        pk = self.submit(self.ram_user, hostel, {"room_type": rt.pk}).data["id"]
        self.assertEqual(self.act(self.office, pk, "approve").status_code, 400)
        allocate_hostel_room(bed=bed, student=self.shyam)
        response = self.act(self.office, pk, "approve", decision={"bed": bed.pk})
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "bed_taken"))
        self.assertEqual(Application.objects.get(pk=pk).status, "in_review")
        bed2 = Bed.objects.create(organization=self.org, room=room, label="B")
        response = self.act(self.office, pk, "approve", decision={"bed": bed2.pk})
        self.assertEqual(response.data["status"], "approved", response.data)
        allocation = Allocation.objects.get(student=self.ram)
        self.assertEqual(response.data["outcome"], {"type": "hostel.allocation", "id": allocation.pk})

    def test_leave_is_applied_and_approved_in_hr(self):
        FiscalYear.objects.create(organization=self.org, name="FY", start_date=date.today() - timedelta(days=100),
                                  end_date=date.today() + timedelta(days=265))
        casual = LeaveType.objects.create(organization=self.org, code="casual", name="Casual", annual_quota=12)
        leave = self.make_type("leave", [("HR", "hr.approve_leave")])
        hr = user_with_permissions(self.org, ["hr.approve_leave", "hr.view"], email="hr@kmc.test")
        day = next_weekday()
        response = self.submit(self.teacher_user, leave, {"leave_type": casual.pk, "start_date": day.isoformat(),
                                                          "end_date": day.isoformat(), "reason": "Family"})
        self.assertEqual(response.status_code, 201, response.data)
        # A student can't use a staff form.
        self.assertEqual(self.submit(self.ram_user, leave, {"leave_type": casual.pk, "start_date": day.isoformat(),
                                                            "end_date": day.isoformat()}).status_code, 400)
        response = self.act(hr, response.data["id"], "approve")
        self.assertEqual(response.data["status"], "approved", response.data)
        request = LeaveRequest.objects.get(staff=self.teacher)
        self.assertEqual((request.status, request.days), ("approved", Decimal("1")))
        self.assertEqual(StaffAttendanceDay.objects.get(staff=self.teacher, date=day).status, "leave")
        # HR's own approvers weren't asked to act on it.
        self.assertFalse(Notification.objects.filter(event_type="LeaveRequested").exists())

    def test_scholarship_granted_once(self):
        merit = Scholarship.objects.create(organization=self.org, name="Merit", kind="percentage", value=50)
        scholarship = self.make_type("scholarship", [("Accounts", "finance.manage")])
        first = self.submit(self.ram_user, scholarship, {"scholarship": merit.pk}).data["id"]
        second = self.submit(self.ram_user, scholarship, {"scholarship": merit.pk}).data["id"]
        self.assertEqual(self.act(self.office, first, "approve").data["status"], "approved")
        self.assertTrue(StudentScholarship.objects.filter(student=self.ram, scholarship=merit).exists())
        response = self.act(self.office, second, "approve")
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "already_granted"))

    def test_transport_full_bus_refuses(self):
        bus = Vehicle.objects.create(organization=self.org, campus=self.campus, registration_number="B1",
                                     name="Bus", capacity=1)
        route = Route.objects.create(organization=self.org, campus=self.campus, code="r", name="R", vehicle=bus)
        stop = Stop.objects.create(organization=self.org, route=route, sequence=1, name="Stop")
        transport = self.make_type("transport", [("Transport office", "transport.manage")])
        mine = self.submit(self.ram_user, transport, {"route": route.pk, "stop": stop.pk}).data["id"]
        other = self.submit(self.office, transport, {"route": route.pk, "stop": stop.pk}, student=self.shyam.pk)
        self.assertEqual(other.status_code, 201, other.data)
        self.assertEqual(self.act(self.office, mine, "approve").data["status"], "approved")
        self.assertTrue(Assignment.objects.filter(student=self.ram).exists())
        # The office applied for Shyam, so someone else decides it.
        response = self.act(self.office, other.data["id"], "approve")
        self.assertEqual((response.status_code, response.data["error"]["code"]), (403, "own_application"))
        response = self.act(self.admin, other.data["id"], "approve")
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "route_full"))

    def test_event_registration(self):
        category = EventCategory.objects.create(organization=self.org, code="academic", name="Academic")
        event = Event.objects.create(organization=self.org, campus=self.campus, name="Science fair", category=category,
                                     start_at=timezone.now() + timedelta(days=5),
                                     end_at=timezone.now() + timedelta(days=5, hours=3), status="published",
                                     registration_mode="approval")
        form = self.make_type("event", [("Coordinator", "events.manage")])
        pk = self.submit(self.ram_user, form, {"event": event.pk}).data["id"]
        response = self.act(self.office, pk, "approve")
        self.assertEqual(response.data["status"], "approved", response.data)
        self.assertEqual(event.registrations.get(student=self.ram).status, "confirmed")

    def test_certificate_issued_revoked_and_seen_by_parent(self):
        form = self.make_type("certificate", [("Office", "applications.certify")],
                              certificate_title="Character certificate")
        pk = self.submit(self.ram_user, form, {"purpose": "Visa"}).data["id"]
        response = self.act(self.office, pk, "approve")
        self.assertEqual(response.data["status"], "approved", response.data)
        certificate = Certificate.objects.get()
        self.assertEqual((certificate.title, certificate.contents["student_name"]),
                         ("Character certificate", self.ram.full_name))
        self.authenticate(self.parent_user)
        self.assertEqual([c["number"] for c in self.client.get(f"{API}/certificates/me/").data],
                         [certificate.number])
        self.authenticate(self.office)
        self.assertEqual(self.client.post(f"{API}/certificates/{certificate.pk}/revoke/", {"reason": "Error"})
                         .data["is_valid"], False)
        response = self.client.post(f"{API}/certificates/", {"student": self.shyam.pk, "title": "Bonafide"})
        self.assertEqual(response.status_code, 201, response.data)
        self.authenticate(self.branch_office)
        self.assertEqual(self.client.post(f"{API}/certificates/", {"student": self.shyam.pk, "title": "x"})
                         .status_code, 403)


class PublicAdmissionTests(ApplicationsTestCase):
    def setUp(self):
        super().setUp()
        self.form = self.make_type("admission", [("Admissions", "admissions.review")], is_public=True)
        self.client.credentials()

    def public(self, verb="", **body):
        return self.client.post(f"{API}/public/organizations/kmc/applications/{verb}", body, format="json")

    def test_apply_check_resubmit_and_approve(self):
        forms = self.client.get(f"{API}/public/organizations/kmc/application-types/")
        self.assertEqual(forms.status_code, 200)
        self.assertEqual([f["id"] for f in forms.data["forms"]], [self.form.pk])
        response = self.public(application_type=self.form.pk, campus=self.campus.pk,
                               contact={"name": "Hari Thapa", "phone": "9800000000"},
                               data={"first_name": "Sita", "last_name": "Thapa", "applying_for": "+2 Science"})
        self.assertEqual(response.status_code, 201, response.data)
        number, token = response.data["number"], response.data["token"]
        self.assertEqual(self.public("status/", number=number, token="wrong").status_code, 404)
        status = self.public("status/", number=number, token=token)
        self.assertEqual(status.data["status"], "in_review")

        pk = Application.objects.get(number=number).pk
        self.act(self.office, pk, "send-back", note="Add date of birth")
        self.client.credentials()
        status = self.public("status/", number=number, token=token)
        self.assertEqual(status.data["history"][-1]["note"], "Add date of birth")
        response = self.public("resubmit/", number=number, token=token,
                               data={"first_name": "Sita", "last_name": "Thapa", "date_of_birth": "2009-02-01"})
        self.assertEqual(response.data["status"], "in_review", response.data)

        response = self.act(self.office, pk, "approve")
        self.assertEqual(response.data["status"], "approved", response.data)
        admission = Admission.objects.get(application_number=number)
        self.assertEqual((admission.status, admission.first_name, str(admission.date_of_birth)),
                         ("approved", "Sita", "2009-02-01"))
        self.client.credentials()
        self.assertEqual(self.public("withdraw/", number=number, token=token).status_code, 409)

    def test_refusals(self):
        self.assertEqual(self.client.get(f"{API}/public/organizations/nope/application-types/").status_code, 404)
        private = self.make_type("general", [("Office", "applications.manage")])
        body = {"campus": self.campus.pk, "contact": {"name": "X", "email": "x@example.com"},
                "data": {"first_name": "A", "last_name": "B"}}
        self.assertEqual(self.public(application_type=private.pk, **body).status_code, 400)
        other = create_organization(code="other")
        their_campus = create_campus(other, code="x")
        self.assertEqual(self.public(**{**body, "application_type": self.form.pk, "campus": their_campus.pk})
                         .status_code, 400)
        self.assertEqual(self.public(application_type=self.form.pk, campus=self.campus.pk,
                                     contact={"name": "X"}, data=body["data"]).status_code, 400)
        # Another organization's URL can't reach this application.
        response = self.public(application_type=self.form.pk, **body)
        response = self.client.post(f"{API}/public/organizations/other/applications/status/",
                                    {"number": response.data["number"], "token": response.data["token"]},
                                    format="json")
        self.assertEqual(response.status_code, 404)

    def test_signed_in_admission_application(self):
        visitor = create_user(self.org, email="visitor@kmc.test", user_type="parent")
        response = self.submit(visitor, self.form, {"first_name": "Gita", "last_name": "KC"})
        self.assertEqual(response.status_code, 400)  # needs a campus
        response = self.submit(visitor, self.form, {"first_name": "Gita", "last_name": "KC"}, campus=self.campus.pk)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["subject_name"], "Gita KC")
