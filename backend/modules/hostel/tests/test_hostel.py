from datetime import date, timedelta
from decimal import Decimal

from modules.finance.models import FeeCategory, Invoice, Scholarship, StudentScholarship
from modules.hostel import services
from modules.hostel.models import Allocation, Bed, Building, Floor, HostelRoom, RoomType
from modules.notifications.models import Notification
from modules.parents.services import link_student
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

API = "/api/v1/hostel"


class HostelTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.branch = create_campus(self.org, code="branch")
        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test", campus=self.campus)
        self.branch_office = user_with_system_role(self.org, "campus-admin", email="branch@kmc.test",
                                                   campus=self.branch)
        self.admin = user_with_system_role(self.org, "org-admin", email="admin@kmc.test")

        self.warden_user = create_user(self.org, email="warden@kmc.test", user_type="staff")
        self.warden = create_staff_member(self.campus, employee_number="E-1", first_name="Hari",
                                          user=self.warden_user, gender="male")
        self.boys = Building.objects.create(organization=self.org, campus=self.campus, code="boys",
                                            name="Boys Hostel", gender="male", warden=self.warden)
        self.ground = Floor.objects.create(organization=self.org, building=self.boys, number=0)
        self.fee = FeeCategory.objects.create(organization=self.org, code="hostel", name="Hostel fee")
        self.double = RoomType.objects.create(organization=self.org, code="double", name="Double",
                                              fee_per_term=Decimal("30000"), fee_category=self.fee)
        self.room = HostelRoom.objects.create(organization=self.org, building=self.boys, floor=self.ground,
                                              number="G01", room_type=self.double)
        self.bed_a = Bed.objects.create(organization=self.org, room=self.room, label="A")
        self.bed_b = Bed.objects.create(organization=self.org, room=self.room, label="B")

        self.ram_user = create_user(self.org, email="ram@kmc.test", user_type="student")
        self.ram = create_student(self.campus, student_number="S-1", first_name="Ram", gender="male",
                                  user=self.ram_user)
        self.shyam = create_student(self.campus, student_number="S-2", first_name="Shyam", gender="male")
        self.sita = create_student(self.campus, student_number="S-3", first_name="Sita", gender="female")

    def allocate(self, user, **body):
        self.authenticate(user)
        for key, value in list(body.items()):
            if isinstance(value, date):
                body[key] = value.isoformat()
        return self.client.post(f"{API}/allocations/", body, format="json")


class StructureTests(HostelTestCase):
    def test_build_a_hostel_through_the_api(self):
        self.authenticate(self.office)
        building = self.client.post(f"{API}/buildings/", {"campus": self.campus.pk, "code": "girls",
                                                          "name": "Girls Hostel", "gender": "female"})
        self.assertEqual(building.status_code, 201, building.data)
        floor = self.client.post(f"{API}/floors/", {"building": building.data["id"], "number": 1})
        self.assertEqual(floor.status_code, 201, floor.data)
        # The same floor twice is refused.
        self.assertEqual(self.client.post(f"{API}/floors/", {"building": building.data["id"], "number": 1})
                         .status_code, 400)
        room = self.client.post(f"{API}/rooms/", {"building": building.data["id"], "floor": floor.data["id"],
                                                  "number": "101", "room_type": self.double.pk})
        self.assertEqual(room.status_code, 201, room.data)
        # A floor of another building is refused.
        bad = self.client.post(f"{API}/rooms/", {"building": building.data["id"], "floor": self.ground.pk,
                                                 "number": "102", "room_type": self.double.pk})
        self.assertEqual(bad.status_code, 400)
        bed = self.client.post(f"{API}/beds/", {"room": room.data["id"], "label": "A"})
        self.assertEqual(bed.status_code, 201, bed.data)
        listed = self.client.get(f"{API}/rooms/{room.data['id']}/")
        self.assertEqual((listed.data["beds"], listed.data["occupied"]), (1, 0))

    def test_codes_are_case_insensitive(self):
        self.authenticate(self.office)
        response = self.client.post(f"{API}/buildings/", {"campus": self.campus.pk, "code": "BOYS", "name": "Dup"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f"{API}/room-types/", {"code": "Single", "name": "Single"})
        self.assertEqual((response.status_code, response.data["code"]), (201, "single"))

    def test_in_use_structure_cannot_be_deleted(self):
        self.authenticate(self.admin)
        self.assertEqual(self.client.delete(f"{API}/buildings/{self.boys.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(f"{API}/floors/{self.ground.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(f"{API}/room-types/{self.double.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(f"{API}/rooms/{self.room.pk}/").status_code, 409)
        services.allocate_hostel_room(bed=self.bed_a, student=self.ram)
        self.assertEqual(self.client.delete(f"{API}/beds/{self.bed_a.pk}/").status_code, 409)
        self.assertEqual(self.client.delete(f"{API}/beds/{self.bed_b.pk}/").status_code, 204)

    def test_campus_admin_of_another_campus_sees_nothing_and_cannot_build_here(self):
        self.authenticate(self.branch_office)
        self.assertEqual(self.client.get(f"{API}/buildings/").data["count"], 0)
        self.assertEqual(self.client.get(f"{API}/beds/").data["count"], 0)
        self.assertEqual(self.client.get(f"{API}/rooms/{self.room.pk}/").status_code, 404)
        response = self.client.post(f"{API}/floors/", {"building": self.boys.pk, "number": 5})
        self.assertEqual(response.status_code, 403)

    def test_another_organizations_rows_are_unknown(self):
        other = create_organization(code="other")
        other_campus = create_campus(other, code="x")
        their = Building.objects.create(organization=other, campus=other_campus, code="b", name="Theirs")
        self.authenticate(self.office)
        self.assertEqual(self.client.post(f"{API}/floors/", {"building": their.pk, "number": 1}).status_code, 400)
        self.assertEqual(self.client.get(f"{API}/buildings/{their.pk}/").status_code, 404)

    def test_available_filter(self):
        services.allocate_hostel_room(bed=self.bed_a, student=self.ram)
        self.authenticate(self.office)
        beds = self.client.get(f"{API}/beds/", {"available": "true"}).data["results"]
        self.assertEqual([b["label"] for b in beds], ["B"])
        services.allocate_hostel_room(bed=self.bed_b, student=self.shyam)
        self.assertEqual(self.client.get(f"{API}/rooms/", {"available": "true"}).data["count"], 0)
        room = self.client.get(f"{API}/rooms/{self.room.pk}/").data
        self.assertEqual(room["occupied"], 2)


class AllocationTests(HostelTestCase):
    def test_reserve_check_in_check_out(self):
        response = self.allocate(self.office, bed=self.bed_a.pk, student=self.ram.pk)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "reserved")
        pk = response.data["id"]
        bed = self.client.get(f"{API}/beds/{self.bed_a.pk}/").data
        self.assertEqual(bed["occupant"]["name"], self.ram.full_name)

        response = self.client.post(f"{API}/allocations/{pk}/check-in/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "checked_in")
        self.assertEqual(self.client.post(f"{API}/allocations/{pk}/check-in/").status_code, 409)
        # Can't check out in the future.
        future = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(self.client.post(f"{API}/allocations/{pk}/check-out/", {"on": future}).status_code, 400)
        response = self.client.post(f"{API}/allocations/{pk}/check-out/", {"note": "Left for home"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["end_date"], date.today().isoformat())
        # The bed is free again.
        self.assertEqual(self.allocate(self.office, bed=self.bed_a.pk, student=self.shyam.pk).status_code, 201)

    def test_a_bed_and_a_person_hold_one_allocation(self):
        self.assertEqual(self.allocate(self.office, bed=self.bed_a.pk, student=self.ram.pk).status_code, 201)
        taken = self.allocate(self.office, bed=self.bed_a.pk, student=self.shyam.pk)
        self.assertEqual((taken.status_code, taken.data["error"]["code"]), (409, "bed_taken"))
        twice = self.allocate(self.office, bed=self.bed_b.pk, student=self.ram.pk)
        self.assertEqual((twice.status_code, twice.data["error"]["code"]), (409, "already_allocated"))

    def test_gender_rule_inactive_beds_and_exactly_one_occupant(self):
        wrong = self.allocate(self.office, bed=self.bed_a.pk, student=self.sita.pk)
        self.assertEqual((wrong.status_code, wrong.data["error"]["code"]), (409, "wrong_gender"))
        self.bed_a.is_active = False
        self.bed_a.save()
        self.assertEqual(self.allocate(self.office, bed=self.bed_a.pk, student=self.ram.pk).status_code, 409)
        self.assertEqual(self.allocate(self.office, bed=self.bed_b.pk).status_code, 400)
        both = self.allocate(self.office, bed=self.bed_b.pk, student=self.ram.pk, staff=self.warden.pk)
        self.assertEqual(both.status_code, 400)

    def test_staff_can_have_a_bed(self):
        response = self.allocate(self.office, bed=self.bed_a.pk, staff=self.warden.pk)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["occupant_name"], self.warden.full_name)

    def test_reservation_for_later_cannot_check_in_early_and_can_be_cancelled(self):
        later = date.today() + timedelta(days=10)
        pk = self.allocate(self.office, bed=self.bed_a.pk, student=self.ram.pk, start_date=later).data["id"]
        response = self.client.post(f"{API}/allocations/{pk}/check-in/")
        self.assertEqual((response.status_code, response.data["error"]["code"]), (409, "too_early"))
        self.assertEqual(self.client.post(f"{API}/allocations/{pk}/cancel/", {"reason": ""}).status_code, 400)
        response = self.client.post(f"{API}/allocations/{pk}/cancel/", {"reason": "Joined as day scholar"})
        self.assertEqual(response.data["status"], "cancelled")

    def test_move_ends_one_stay_and_starts_another(self):
        start = date.today() - timedelta(days=20)
        allocation = services.allocate_hostel_room(bed=self.bed_a, student=self.ram, start_date=start)
        services.check_in(allocation)
        self.authenticate(self.office)
        response = self.client.post(f"{API}/allocations/{allocation.pk}/move/", {"bed": self.bed_b.pk})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "checked_in")
        old = Allocation.objects.get(pk=allocation.pk)
        self.assertEqual((old.status, old.end_date), ("checked_out", date.today() - timedelta(days=1)))
        self.assertEqual(self.client.get(f"{API}/allocations/", {"student": self.ram.pk}).data["count"], 2)

    def test_allocations_are_history_not_editable(self):
        pk = self.allocate(self.office, bed=self.bed_a.pk, student=self.ram.pk).data["id"]
        # A superuser skips the permission check, so this shows the route itself refuses.
        self.authenticate(create_superuser())
        self.assertEqual(self.client.patch(f"{API}/allocations/{pk}/", {"note": "x"}).status_code, 405)
        self.assertEqual(self.client.delete(f"{API}/allocations/{pk}/").status_code, 405)

    def test_branch_admin_cannot_allocate_here_or_see_who_holds_a_bed(self):
        services.allocate_hostel_room(bed=self.bed_a, student=self.ram)
        branch_student = create_student(self.branch, student_number="S-9", gender="male")
        response = self.allocate(self.branch_office, bed=self.bed_a.pk, student=branch_student.pk)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Ram", str(response.data))

    def test_without_permission(self):
        clerk = user_with_permissions(self.org, ["hostel.view"], email="clerk@kmc.test")
        self.assertEqual(self.allocate(clerk, bed=self.bed_a.pk, student=self.ram.pk).status_code, 403)
        self.assertEqual(self.client.get(f"{API}/allocations/").status_code, 200)

    def test_resident_and_parent_see_their_own(self):
        allocation = services.allocate_hostel_room(bed=self.bed_a, student=self.ram)
        services.allocate_hostel_room(bed=self.bed_b, student=self.shyam)
        self.authenticate(self.ram_user)
        mine = self.client.get(f"{API}/allocations/me/")
        self.assertEqual([a["id"] for a in mine.data], [allocation.pk])
        self.assertEqual(self.client.get(f"{API}/allocations/").status_code, 403)

        parent_user = create_user(self.org, email="dad@kmc.test", user_type="parent")
        parent = create_parent(self.org, first_name="Dad", user=parent_user)
        link_student(parent=parent, student=self.ram, relationship="father")
        self.authenticate(parent_user)
        self.assertEqual([a["id"] for a in self.client.get(f"{API}/allocations/me/").data], [allocation.pk])


class ComplaintTests(HostelTestCase):
    def test_resident_raises_and_office_resolves(self):
        allocation = services.allocate_hostel_room(bed=self.bed_a, student=self.ram)
        self.authenticate(self.ram_user)
        # Not checked in yet.
        self.assertEqual(self.client.post(f"{API}/complaints/me/", {"title": "Fan broken"}).status_code, 404)
        services.check_in(allocation)
        response = self.client.post(f"{API}/complaints/me/", {"title": "Fan broken", "category": "maintenance",
                                                              "description": "Ceiling fan doesn't spin"})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["building"], response.data["room"]), (self.boys.pk, self.room.pk))
        pk = response.data["id"]
        # The warden is told.
        self.assertTrue(Notification.objects.filter(recipient=self.warden_user, event_type="hostel.complaint")
                        .exists())

        self.authenticate(self.office)
        response = self.client.post(f"{API}/complaints/{pk}/assign/", {"staff": self.warden.pk})
        self.assertEqual(response.data["status"], "in_progress")
        self.assertEqual(self.client.post(f"{API}/complaints/{pk}/resolve/", {"resolution": ""}).status_code, 400)
        response = self.client.post(f"{API}/complaints/{pk}/resolve/", {"resolution": "Capacitor replaced"})
        self.assertEqual(response.data["status"], "resolved")
        self.assertEqual(self.client.post(f"{API}/complaints/{pk}/reject/", {"reason": "x"}).status_code, 409)

        self.authenticate(self.ram_user)
        mine = self.client.get(f"{API}/complaints/me/").data
        self.assertEqual([(c["id"], c["status"]) for c in mine], [(pk, "resolved")])
        self.assertTrue(Notification.objects.filter(recipient=self.ram_user,
                                                    event_type="hostel.complaint_updated").exists())

    def test_office_records_a_complaint(self):
        self.authenticate(self.office)
        response = self.client.post(f"{API}/complaints/", {"building": self.boys.pk, "room": self.room.pk,
                                                           "title": "Water leak", "category": "maintenance"})
        self.assertEqual(response.status_code, 201, response.data)
        self.authenticate(self.branch_office)
        self.assertEqual(self.client.post(f"{API}/complaints/", {"building": self.boys.pk, "title": "x"})
                         .status_code, 403)


class FeeTests(HostelTestCase):
    def setUp(self):
        super().setUp()
        self.year = create_academic_year(self.org)
        self.term = create_term(self.year)

    def bill(self, user=None):
        self.authenticate(user or self.admin)
        return self.client.post(f"{API}/allocations/generate-invoices/", {"term": self.term.pk})

    def test_whole_term_stay_is_billed_once_and_tuition_still_can_be(self):
        services.allocate_hostel_room(bed=self.bed_a, student=self.ram, start_date=self.term.start_date)
        response = self.bill()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["created"], 1)
        invoice = Invoice.objects.get(student=self.ram)
        self.assertEqual((invoice.source, invoice.total, invoice.term_id), ("hostel", Decimal("30000"), self.term.pk))
        # Rerunning skips them.
        self.assertEqual(self.bill().data, {"created": 0, "skipped": 1, "not_enrolled": 0,
                                            "room_types_without_fee_category": []})
        # Finance's own tuition run isn't blocked by the hostel invoice.
        from modules.finance.services import generate_term_invoices
        from tests.factories import create_program, create_section
        from modules.students.services import place_student
        from modules.finance.models import FeeStructure, FeeStructureItem

        program = create_program(self.org)
        section = create_section(self.campus, program, self.year)
        place_student(student=self.ram, section=section)
        tuition = FeeCategory.objects.create(organization=self.org, code="tuition", name="Tuition")
        structure = FeeStructure.objects.create(organization=self.org, program=program, level=11,
                                                academic_year=self.year)
        FeeStructureItem.objects.create(organization=self.org, fee_structure=structure, category=tuition,
                                        amount=50000, frequency="per_term")
        self.assertEqual(generate_term_invoices(structure, self.term, by=self.admin)["created"], 1)

    def test_partial_stays_are_prorated_per_stay(self):
        days = (self.term.end_date - self.term.start_date).days + 1
        half = self.term.start_date + timedelta(days=days // 2 - 1)
        stay = services.allocate_hostel_room(bed=self.bed_a, student=self.ram, start_date=self.term.start_date)
        stay.status, stay.end_date = "checked_out", half
        stay.save()
        services.allocate_hostel_room(bed=self.bed_b, student=self.ram, start_date=half + timedelta(days=1))
        self.bill()
        invoice = Invoice.objects.get(student=self.ram)
        amounts = sorted(invoice.items.values_list("amount", flat=True))
        self.assertEqual(len(amounts), 2)
        self.assertAlmostEqual(sum(amounts), Decimal("30000"), delta=Decimal("0.02"))

    def test_staff_cancelled_and_uncategorised_are_not_billed(self):
        services.allocate_hostel_room(bed=self.bed_a, staff=self.warden)
        cancelled = services.allocate_hostel_room(bed=self.bed_b, student=self.shyam)
        services.cancel_allocation(cancelled, "changed mind")
        free = RoomType.objects.create(organization=self.org, code="free", name="Free")
        room = HostelRoom.objects.create(organization=self.org, building=self.boys, floor=self.ground,
                                         number="G02", room_type=free)
        services.allocate_hostel_room(bed=Bed.objects.create(organization=self.org, room=room, label="A"),
                                      student=self.ram)
        response = self.bill()
        self.assertEqual(response.data["created"], 0)
        self.assertEqual(response.data["room_types_without_fee_category"], ["Free"])
        self.assertFalse(Invoice.objects.exists())

    def test_only_a_hostel_scholarship_discounts_it(self):
        services.allocate_hostel_room(bed=self.bed_a, student=self.ram, start_date=self.term.start_date)
        everything = Scholarship.objects.create(organization=self.org, name="Merit", kind="percentage", value=50)
        hostel_only = Scholarship.objects.create(organization=self.org, name="Hostel concession", kind="flat",
                                                 value=5000, category=self.fee)
        for s in (everything, hostel_only):
            StudentScholarship.objects.create(organization=self.org, student=self.ram, scholarship=s,
                                              started_on=self.term.start_date)
        self.bill()
        self.assertEqual(Invoice.objects.get(student=self.ram).total, Decimal("25000"))

    def test_needs_finance_permission_and_respects_campus(self):
        services.allocate_hostel_room(bed=self.bed_a, student=self.ram, start_date=self.term.start_date)
        warden_only = user_with_permissions(self.org, ["hostel.manage"], email="w@kmc.test")
        self.assertEqual(self.bill(warden_only).status_code, 403)
        # The branch's office bills only the branch's hostels.
        self.assertEqual(self.bill(self.branch_office).data["created"], 0)
        self.assertEqual(self.bill(self.office).data["created"], 1)
