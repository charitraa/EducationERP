from datetime import date, timedelta
from decimal import Decimal

from modules.finance.models import FeeCategory, Invoice
from modules.notifications.models import Notification
from modules.parents.services import link_student
from modules.transport import services
from modules.transport.models import Assignment, Driver, Route, Stop, TripRecord, Vehicle, VehicleDocument
from tests.base import APITestCaseBase
from tests.factories import (
    create_academic_year,
    create_campus,
    create_organization,
    create_parent,
    create_staff_member,
    create_student,
    create_superuser,
    create_term,
    create_user,
    user_with_permissions,
    user_with_system_role,
)

API = "/api/v1/transport"


class TransportTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.branch = create_campus(self.org, code="branch")
        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test", campus=self.campus)
        self.branch_office = user_with_system_role(self.org, "campus-admin", email="branch@kmc.test",
                                                   campus=self.branch)
        self.admin = user_with_system_role(self.org, "org-admin", email="admin@kmc.test")

        self.driver_user = create_user(self.org, email="driver@kmc.test", user_type="staff")
        self.driver_staff = create_staff_member(self.campus, employee_number="D-1", first_name="Bikash",
                                                user=self.driver_user)
        self.driver = Driver.objects.create(organization=self.org, staff=self.driver_staff, license_number="L-1")
        self.helper_user = create_user(self.org, email="helper@kmc.test", user_type="staff")
        self.helper = Driver.objects.create(
            organization=self.org, role="assistant",
            staff=create_staff_member(self.campus, employee_number="D-2", first_name="Suman", user=self.helper_user))
        self.bus = Vehicle.objects.create(organization=self.org, campus=self.campus, registration_number="BA 2 KHA 1",
                                          name="Bus 1", capacity=2)
        self.fee = FeeCategory.objects.create(organization=self.org, code="bus", name="Bus fee")
        self.route = Route.objects.create(organization=self.org, campus=self.campus, code="r1", name="Koteshwor",
                                          vehicle=self.bus, driver=self.driver, assistant=self.helper,
                                          fee_per_term=Decimal("12000"), fee_category=self.fee)
        self.near = Stop.objects.create(organization=self.org, route=self.route, sequence=1, name="Tinkune")
        self.far = Stop.objects.create(organization=self.org, route=self.route, sequence=2, name="Jadibuti",
                                       fee_per_term=Decimal("15000"))

        self.ram_user = create_user(self.org, email="ram@kmc.test", user_type="student")
        self.ram = create_student(self.campus, student_number="S-1", first_name="Ram", user=self.ram_user)
        self.shyam = create_student(self.campus, student_number="S-2", first_name="Shyam")
        self.hari = create_student(self.campus, student_number="S-3", first_name="Hari")

    def assign(self, user, **body):
        self.authenticate(user)
        body = {"route": self.route.pk, "stop": self.near.pk, **body}
        for key, value in list(body.items()):
            if isinstance(value, date):
                body[key] = value.isoformat()
        return self.client.post(f"{API}/assignments/", body, format="json")


class FleetTests(TransportTestCase):
    def test_vehicles_papers_and_crew(self):
        self.authenticate(self.office)
        response = self.client.post(f"{API}/vehicles/", {"campus": self.campus.pk, "registration_number": "ba 3 kha 9",
                                                         "name": "Van", "kind": "van", "capacity": 12})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["registration_number"], "BA 3 KHA 9")
        dup = self.client.post(f"{API}/vehicles/", {"campus": self.campus.pk, "registration_number": "BA 3 KHA 9",
                                                    "name": "Van 2", "capacity": 12})
        self.assertEqual(dup.status_code, 400)
        soon = date.today() + timedelta(days=10)
        doc = self.client.post(f"{API}/vehicle-documents/", {"vehicle": response.data["id"], "kind": "insurance",
                                                              "expires_on": soon.isoformat()})
        self.assertEqual(doc.status_code, 201, doc.data)
        VehicleDocument.objects.create(organization=self.org, vehicle=self.bus, kind="bluebook",
                                       expires_on=date.today() + timedelta(days=400))
        expiring = self.client.get(f"{API}/vehicle-documents/", {"expiring_within": 30}).data["results"]
        self.assertEqual([d["kind"] for d in expiring], ["insurance"])
        self.assertEqual(self.client.get(f"{API}/vehicle-documents/", {"expiring_within": "x"}).status_code, 400)

        # Crew: one entry per staff member; an assistant can't drive a route.
        self.assertEqual(self.client.post(f"{API}/drivers/", {"staff": self.driver_staff.pk}).status_code, 400)
        response = self.client.patch(f"{API}/routes/{self.route.pk}/", {"driver": self.helper.pk,
                                                                        "assistant": None})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.patch(f"{API}/drivers/{self.driver.pk}/", {"role": "assistant"}).status_code,
                         400)

    def test_route_rules(self):
        self.authenticate(self.office)
        branch_bus = Vehicle.objects.create(organization=self.org, campus=self.branch, registration_number="X",
                                            name="Branch bus", capacity=10)
        response = self.client.patch(f"{API}/routes/{self.route.pk}/", {"vehicle": branch_bus.pk})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post(f"{API}/stops/", {"route": self.route.pk, "sequence": 1, "name": "Dup"})
                         .status_code, 400)
        route = self.client.get(f"{API}/routes/{self.route.pk}/").data
        self.assertEqual([s["name"] for s in route["stops"]], ["Tinkune", "Jadibuti"])
        self.assertEqual([s["fee"] for s in route["stops"]], ["12000.00", "15000.00"])

    def test_route_code_is_case_insensitive(self):
        self.authenticate(self.office)
        response = self.client.post(f"{API}/routes/", {"campus": self.campus.pk, "code": "R1", "name": "Dup"})
        self.assertEqual(response.status_code, 400)

    def test_in_use_cannot_be_deleted(self):
        self.authenticate(self.admin)
        for path in (f"vehicles/{self.bus.pk}", f"drivers/{self.driver.pk}", f"routes/{self.route.pk}"):
            self.assertEqual(self.client.delete(f"{API}/{path}/").status_code, 409, path)
        services.assign_rider(route=self.route, stop=self.near, student=self.ram)
        self.assertEqual(self.client.delete(f"{API}/stops/{self.near.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(f"{API}/stops/{self.far.pk}/").status_code, 204)

    def test_campus_scoping(self):
        self.authenticate(self.branch_office)
        self.assertEqual(self.client.get(f"{API}/routes/").data["count"], 0)
        self.assertEqual(self.client.get(f"{API}/vehicles/{self.bus.pk}/").status_code, 404)
        self.assertEqual(self.client.post(f"{API}/stops/", {"route": self.route.pk, "sequence": 9, "name": "x"})
                         .status_code, 403)
        self.assertEqual(self.client.post(f"{API}/maintenance/", {
            "vehicle": self.bus.pk, "kind": "service", "date": date.today().isoformat(), "description": "x"})
            .status_code, 403)

    def test_maintenance_and_fuel(self):
        self.authenticate(self.office)
        response = self.client.post(f"{API}/maintenance/", {
            "vehicle": self.bus.pk, "kind": "service", "date": date.today().isoformat(), "odometer": 42000,
            "cost": "8500", "description": "Oil change", "next_due_odometer": 47000})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["recorded_by"], self.office.pk)
        response = self.client.post(f"{API}/fuel-logs/", {"vehicle": self.bus.pk, "date": date.today().isoformat(),
                                                          "litres": "0", "cost": "100"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f"{API}/fuel-logs/", {"vehicle": self.bus.pk, "date": date.today().isoformat(),
                                                          "litres": "40", "cost": "6400", "odometer": 42010})
        self.assertEqual(response.status_code, 201, response.data)


class RiderTests(TransportTestCase):
    def test_assign_until_full(self):
        self.assertEqual(self.assign(self.office, student=self.ram.pk).status_code, 201)
        self.assertEqual(self.assign(self.office, student=self.shyam.pk, stop=self.far.pk).status_code, 201)
        full = self.assign(self.office, student=self.hari.pk)
        self.assertEqual((full.status_code, full.data["error"]["code"]), (409, "route_full"))
        twice = self.assign(self.office, student=self.ram.pk)
        self.assertEqual((twice.status_code, twice.data["error"]["code"]), (409, "already_assigned"))
        route = self.client.get(f"{API}/routes/{self.route.pk}/").data
        self.assertEqual(route["riders"], 2)

    def test_ending_frees_the_seat_from_the_next_day(self):
        assignment = services.assign_rider(route=self.route, stop=self.near, student=self.ram,
                                           start_date=date.today() - timedelta(days=30))
        services.assign_rider(route=self.route, stop=self.near, student=self.shyam)
        self.authenticate(self.office)
        response = self.client.post(f"{API}/assignments/{assignment.pk}/end/", {"reason": "Moved house"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["end_date"], date.today().isoformat())
        # Still riding today, so the bus is full today but free tomorrow.
        self.assertEqual(self.assign(self.office, student=self.hari.pk).status_code, 409)
        tomorrow = date.today() + timedelta(days=1)
        self.assertEqual(self.assign(self.office, student=self.hari.pk, start_date=tomorrow).status_code, 201)
        # Ram can join another route only after his last day.
        self.assertEqual(self.assign(self.office, student=self.ram.pk, start_date=date.today()).status_code, 409)

    def test_wrong_stop_staff_rider_and_one_rider_only(self):
        other = Route.objects.create(organization=self.org, campus=self.campus, code="r2", name="Other")
        other_stop = Stop.objects.create(organization=self.org, route=other, sequence=1, name="Elsewhere")
        self.assertEqual(self.assign(self.office, student=self.ram.pk, stop=other_stop.pk).status_code, 400)
        self.assertEqual(self.assign(self.office).status_code, 400)
        teacher = create_staff_member(self.campus, employee_number="T-1")
        response = self.assign(self.office, staff=teacher.pk, direction="drop")
        self.assertEqual(response.status_code, 201, response.data)

    def test_history_not_editable(self):
        pk = self.assign(self.office, student=self.ram.pk).data["id"]
        self.authenticate(create_superuser())
        self.assertEqual(self.client.patch(f"{API}/assignments/{pk}/", {"direction": "drop"}).status_code, 405)
        self.assertEqual(self.client.delete(f"{API}/assignments/{pk}/").status_code, 405)

    def test_rider_and_parent_see_their_own(self):
        mine = services.assign_rider(route=self.route, stop=self.far, student=self.ram)
        services.assign_rider(route=self.route, stop=self.near, student=self.shyam)
        self.authenticate(self.ram_user)
        response = self.client.get(f"{API}/assignments/me/")
        self.assertEqual([(a["id"], a["stop_name"]) for a in response.data], [(mine.pk, "Jadibuti")])
        self.assertEqual(self.client.get(f"{API}/assignments/").status_code, 403)
        parent_user = create_user(self.org, email="mum@kmc.test", user_type="parent")
        link_student(parent=create_parent(self.org, user=parent_user), student=self.ram, relationship="mother")
        self.authenticate(parent_user)
        self.assertEqual([a["id"] for a in self.client.get(f"{API}/assignments/me/").data], [mine.pk])


class TripTests(TransportTestCase):
    def setUp(self):
        super().setUp()
        self.ram_ride = services.assign_rider(route=self.route, stop=self.far, student=self.ram)
        self.shyam_ride = services.assign_rider(route=self.route, stop=self.near, student=self.shyam,
                                                direction="drop")
        self.parent_user = create_user(self.org, email="dad@kmc.test", user_type="parent")
        link_student(parent=create_parent(self.org, user=self.parent_user), student=self.ram,
                     relationship="father")

    def open(self, user, direction="pickup", **extra):
        self.authenticate(user)
        return self.client.post(f"{API}/trips/", {"route": self.route.pk, "direction": direction, **extra})

    def test_crew_runs_their_trip(self):
        response = self.open(self.driver_user)
        self.assertEqual(response.status_code, 201, response.data)
        trip = response.data["id"]
        # Only Ram rides the morning run; Shyam is drop-only.
        self.assertEqual([r["rider_name"] for r in response.data["roster"]], [self.ram.full_name])
        # Opening again returns the same trip.
        again = self.open(self.driver_user)
        self.assertEqual((again.status_code, again.data["id"]), (200, trip))
        mine = self.client.get(f"{API}/trips/mine/").data
        self.assertEqual(mine["routes"][0]["pickup_trip"], trip)

        response = self.client.post(f"{API}/trips/{trip}/mark/", {"entries": [
            {"assignment": self.ram_ride.pk, "status": "absent"}]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["roster"][0]["status"], "absent")
        self.assertTrue(Notification.objects.filter(recipient=self.parent_user, event_type="transport.absent")
                        .exists())
        # Shyam isn't on this run.
        response = self.client.post(f"{API}/trips/{trip}/mark/", {"entries": [
            {"assignment": self.shyam_ride.pk, "status": "boarded"}]}, format="json")
        self.assertEqual((response.status_code, response.data["error"]["code"]), (400, "not_a_rider"))

        self.assertEqual(self.client.post(f"{API}/trips/{trip}/complete/").status_code, 200)
        # After completion the crew can't change marks; the office can.
        late = {"entries": [{"assignment": self.ram_ride.pk, "status": "boarded", "at": "07:15"}]}
        self.assertEqual(self.client.post(f"{API}/trips/{trip}/mark/", late, format="json").status_code, 403)
        self.authenticate(self.office)
        self.assertEqual(self.client.post(f"{API}/trips/{trip}/mark/", late, format="json").status_code, 200)
        self.assertEqual(TripRecord.objects.get().status, "boarded")
        # Only one absence notice: the correction to boarded sends none.
        self.assertEqual(Notification.objects.filter(event_type="transport.absent").count(), 1)

    def test_others_cannot_run_the_trip(self):
        stranger = create_user(self.org, email="teacher@kmc.test", user_type="staff")
        create_staff_member(self.campus, employee_number="T-9", user=stranger)
        self.assertEqual(self.open(stranger).status_code, 403)
        trip, _ = services.open_trip(route=self.route, date=date.today(), direction="pickup", by=self.driver_user)
        self.authenticate(stranger)
        self.assertEqual(self.client.post(f"{API}/trips/{trip.pk}/complete/").status_code, 403)
        self.assertEqual(self.client.get(f"{API}/trips/{trip.pk}/").status_code, 403)
        self.assertEqual(self.open(self.branch_office).status_code, 403)
        future = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(self.open(self.driver_user, date=future).status_code, 400)

    def test_another_organizations_trip_is_unknown(self):
        trip, _ = services.open_trip(route=self.route, date=date.today(), direction="pickup", by=self.driver_user)
        other = create_organization(code="other")
        outsider = user_with_permissions(other, ["transport.view", "transport.manage"], email="x@other.test")
        self.authenticate(outsider)
        self.assertEqual(self.client.post(f"{API}/trips/{trip.pk}/complete/").status_code, 404)
        self.assertEqual(self.client.post(f"{API}/trips/", {"route": self.route.pk, "direction": "drop"})
                         .status_code, 400)

    def test_boarding_history(self):
        trip, _ = services.open_trip(route=self.route, date=date.today(), direction="pickup", by=self.driver_user)
        services.mark_trip(trip, [{"assignment": self.ram_ride, "status": "boarded"}], by=self.driver_user)
        self.authenticate(self.parent_user)
        rows = self.client.get(f"{API}/trip-records/me/").data["results"]
        self.assertEqual([(r["rider_name"], r["status"]) for r in rows], [(self.ram.full_name, "boarded")])
        self.authenticate(self.office)
        self.assertEqual(self.client.get(f"{API}/trip-records/", {"assignment__student": self.ram.pk})
                         .data["count"], 1)


class FeeTests(TransportTestCase):
    def setUp(self):
        super().setUp()
        self.year = create_academic_year(self.org)
        self.term = create_term(self.year)

    def test_stop_fee_overrides_route_fee_and_rerun_is_safe(self):
        services.assign_rider(route=self.route, stop=self.near, student=self.ram, start_date=self.term.start_date)
        services.assign_rider(route=self.route, stop=self.far, student=self.shyam, start_date=self.term.start_date)
        teacher = create_staff_member(self.campus, employee_number="T-1")
        self.bus.capacity = 5
        self.bus.save()
        services.assign_rider(route=self.route, stop=self.near, staff=teacher)
        self.authenticate(self.office)
        response = self.client.post(f"{API}/assignments/generate-invoices/", {"term": self.term.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["created"], 2)
        totals = {i.student_id: i.total for i in Invoice.objects.filter(source="transport")}
        self.assertEqual(totals, {self.ram.pk: Decimal("12000"), self.shyam.pk: Decimal("15000")})
        again = self.client.post(f"{API}/assignments/generate-invoices/", {"term": self.term.pk})
        self.assertEqual((again.data["created"], again.data["skipped"]), (0, 2))

    def test_joining_mid_term_is_prorated(self):
        days = (self.term.end_date - self.term.start_date).days + 1
        joined = self.term.end_date - timedelta(days=days // 4 - 1)
        services.assign_rider(route=self.route, stop=self.near, student=self.ram, start_date=joined)
        services.generate_term_invoices(self.term, by=self.admin)
        expected = (Decimal("12000") * (days // 4) / days).quantize(Decimal("0.01"))
        self.assertEqual(Invoice.objects.get(student=self.ram).total, expected)

    def test_permission(self):
        clerk = user_with_permissions(self.org, ["transport.manage"], email="clerk@kmc.test")
        self.authenticate(clerk)
        response = self.client.post(f"{API}/assignments/generate-invoices/", {"term": self.term.pk})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Assignment.objects.exists())
