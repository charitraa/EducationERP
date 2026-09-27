"""Exam rooms, seating plans, invigilators and admit cards."""
from datetime import timedelta

from tests.factories import (
    create_attendance_record,
    create_attendance_session,
    create_room,
    create_user,
)

from ..models import AdmitCard, SeatAllocation
from .base import API, TODAY, ExamTestCase

EXAMS = f"{API}/exams/"


class SeatingTests(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.exam = self.make_exam()
        self.room1 = create_room(self.campus, code="r1", name="Hall 1", capacity=2)
        self.room2 = create_room(self.campus, code="r2", name="Hall 2", capacity=2)
        self.login(self.office)

    def add_rooms(self, *rooms):
        for room in rooms:
            r = self.client.post(f"{API}/exam-rooms/", {"exam": self.exam.pk, "room": room.pk})
            self.assertEqual(r.status_code, 201, r.data)

    def plan(self, **body):
        return self.client.post(f"{EXAMS}{self.exam.pk}/seat-plan/", body)

    def test_interleaving_seats_neighbours_from_different_classes(self):
        self.add_rooms(self.room1, self.room2)
        r = self.plan()

        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["students"], r.data["seats"]), (4, 4))
        seats = self.client.get(f"{API}/seat-allocations/", {"exam": self.exam.pk}).data["results"]
        by_room = {}
        for s in seats:
            by_room.setdefault(s["room_name"], []).append((s["seat_number"], s["section_name"]))
        for room, taken in by_room.items():
            self.assertEqual(len({section for _, section in taken}), 2, room)     # A next to B

    def test_sequential_keeps_classes_together(self):
        self.add_rooms(self.room1, self.room2)
        self.plan(strategy="sequential")
        rooms = {s.exam_room.room.name: {s.enrollment.section.name for s in SeatAllocation.objects.filter(exam_room=s.exam_room)}
                 for s in SeatAllocation.objects.all()}
        self.assertEqual(rooms, {"Hall 1": {"A"}, "Hall 2": {"B"}})

    def test_not_enough_seats_says_how_many(self):
        self.add_rooms(self.room1)
        r = self.plan()
        self.assertError(r, 409, "not_enough_seats")
        self.assertEqual(r.data["error"]["details"], {"students": 4, "seats": 2})
        self.assertFalse(SeatAllocation.objects.exists())

    def test_a_dry_run_saves_nothing(self):
        self.add_rooms(self.room1, self.room2)
        self.assertTrue(self.plan(dry_run=True).data["dry_run"])
        self.assertFalse(SeatAllocation.objects.exists())

    def test_planning_again_replaces_the_plan(self):
        self.add_rooms(self.room1, self.room2)
        self.plan()
        self.plan(strategy="sequential")
        self.assertEqual(SeatAllocation.objects.count(), 4)

    def test_it_needs_rooms_with_a_capacity_and_a_scheduled_exam(self):
        self.assertError(self.plan(), 400, "no_rooms")
        no_capacity = create_room(self.campus, code="r3", name="Hall 3")
        self.add_rooms(no_capacity)
        self.assertError(self.plan(), 400, "no_capacity")
        draft = self.make_exam("Draft", status="draft")
        r = self.client.post(f"{EXAMS}{draft.pk}/seat-plan/", {})
        self.assertError(r, 409, "not_scheduled")

    def test_an_exam_room_can_override_the_rooms_capacity(self):
        r = self.client.post(f"{API}/exam-rooms/", {"exam": self.exam.pk, "room": self.room1.pk, "capacity": 10})
        self.assertEqual(r.data["seats"], 10)
        self.assertEqual(self.plan().data["rooms"], [{"room": "Hall 1", "students": 4}])

    def test_a_room_of_another_campus_or_used_twice_is_refused(self):
        other = create_room(self.other_campus, code="x", name="Elsewhere", capacity=30)
        self.assertEqual(self.client.post(f"{API}/exam-rooms/", {"exam": self.exam.pk, "room": other.pk}).status_code, 400)
        self.add_rooms(self.room1)
        self.assertEqual(self.client.post(f"{API}/exam-rooms/", {"exam": self.exam.pk, "room": self.room1.pk}).status_code, 400)

    def test_clearing_the_plan(self):
        self.add_rooms(self.room1, self.room2)
        self.plan()
        self.assertEqual(self.client.post(f"{EXAMS}{self.exam.pk}/clear-seat-plan/").status_code, 204)
        self.assertFalse(SeatAllocation.objects.exists())


class InvigilationTests(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.exam = self.make_exam(papers=(("computer", 3), ("biology", 3)))       # same slot, no shared students
        self.room1 = create_room(self.campus, code="r1", name="Hall 1", capacity=30)
        self.room2 = create_room(self.campus, code="r2", name="Hall 2", capacity=30)
        self.login(self.office)
        self.er1 = self.client.post(f"{API}/exam-rooms/", {"exam": self.exam.pk, "room": self.room1.pk}).data["id"]
        self.er2 = self.client.post(f"{API}/exam-rooms/", {"exam": self.exam.pk, "room": self.room2.pk}).data["id"]
        self.computer_paper = self.paper_of(self.exam, self.computer)
        self.biology_paper = self.paper_of(self.exam, self.biology)

    def assign(self, paper, exam_room, staff, **extra):
        return self.client.post(f"{API}/invigilations/", {"exam_subject": paper.pk, "exam_room": exam_room,
                                                          "staff": staff.pk, **extra})

    def test_assign_an_invigilator(self):
        r = self.assign(self.computer_paper, self.er1, self.hari, is_chief=True)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["staff_name"], r.data["subject_name"], r.data["room_name"]),
                         ("Hari Staff", "Computer Science", "Hall 1"))

    def test_nobody_watches_two_rooms_at_once(self):
        self.assign(self.computer_paper, self.er1, self.hari)
        r = self.assign(self.biology_paper, self.er2, self.hari)          # same time slot
        self.assertError(r, 409, "invigilator_clash")
        self.assertEqual(self.assign(self.biology_paper, self.er2, self.sita).status_code, 201)

    def test_the_same_person_can_watch_back_to_back_papers(self):
        later = self.paper(self.exam, self.math, days_ago=3, start="12:00", end="14:00")
        self.assign(self.computer_paper, self.er1, self.hari)
        self.assertEqual(self.assign(later, self.er1, self.hari).status_code, 201)

    def test_a_clash_across_two_exams_is_caught_too(self):
        other = self.make_exam("Other exam", papers=(("physics", 3),))
        other_room = self.client.post(f"{API}/exam-rooms/", {"exam": other.pk, "room": self.room1.pk}).data["id"]
        self.assign(self.computer_paper, self.er1, self.hari)
        self.assertError(self.assign(self.paper_of(other, self.physics), other_room, self.hari), 409, "invigilator_clash")

    def test_the_room_must_belong_to_the_papers_exam(self):
        other = self.make_exam("Other exam", papers=(("physics", 4),))
        other_room = self.client.post(f"{API}/exam-rooms/", {"exam": other.pk, "room": self.room1.pk}).data["id"]
        self.assertEqual(self.assign(self.computer_paper, other_room, self.hari).status_code, 400)

    def test_duplicate_assignment_is_refused(self):
        self.assign(self.computer_paper, self.er1, self.hari)
        self.assertEqual(self.assign(self.computer_paper, self.er1, self.hari).status_code, 400)


class AdmitCardTests(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.exam = self.make_exam(min_attendance_percent=75, instructions="Bring your own calculator.")
        self.login(self.office)

    def generate(self, **body):
        return self.client.post(f"{EXAMS}{self.exam.pk}/generate-admit-cards/", body)

    def take_attendance(self, section, days):
        """One roll call a day for ``section``: ``days`` is a list of
        {student: status} dicts, oldest first."""
        for i, statuses in enumerate(days):
            session = create_attendance_session(section, day=TODAY - timedelta(days=40 - i))
            for student, status in statuses.items():
                create_attendance_record(session, self.enrollment(student, session.date), status)

    def test_every_candidate_gets_a_numbered_card(self):
        r = self.generate()

        self.assertEqual((r.status_code, r.data), (200, {"created": 4, "withheld": 0, "skipped": 0}))
        numbers = list(AdmitCard.objects.values_list("card_number", flat=True))
        self.assertEqual(len(set(numbers)), 4)
        self.assertTrue(all(n.startswith(f"EX{self.exam.pk}-") for n in numbers))

    def test_running_it_again_only_adds_new_candidates(self):
        self.generate()
        self.assertEqual(self.generate().data, {"created": 0, "withheld": 0, "skipped": 4})

    def test_low_attendance_gets_a_withheld_card_with_the_reason(self):
        days = [{self.ram: "present" if i < 6 else "absent", self.shyam: "present" if i < 9 else "absent"}
                for i in range(10)]                                      # Ram 60%, Shyam 90%
        self.take_attendance(self.section_a, days)
        self.assertEqual(self.generate().data["withheld"], 1)

        card = AdmitCard.objects.get(student=self.ram)
        self.assertEqual(card.status, "withheld")
        self.assertIn("60.0%", card.withheld_reason)
        self.assertEqual(AdmitCard.objects.get(student=self.shyam).status, "issued")
        self.assertEqual(AdmitCard.objects.get(student=self.gita).status, "issued")      # no attendance taken: not held back

    def test_excused_absences_dont_count_against_the_student(self):
        self.take_attendance(self.section_a, [{self.ram: "present" if i < 4 else "medical_leave"} for i in range(10)])
        self.generate()
        self.assertEqual(AdmitCard.objects.get(student=self.ram).status, "issued")      # 4 of 4 counted

    def test_release_and_withhold_by_hand(self):
        self.generate()
        card = AdmitCard.objects.get(student=self.binu)
        url = f"{API}/admit-cards/{card.pk}"

        self.assertEqual(self.client.post(f"{url}/withhold/", {}).status_code, 400)
        r = self.client.post(f"{url}/withhold/", {"reason": "Fees unpaid"})
        self.assertEqual((r.data["status"], r.data["withheld_reason"]), ("withheld", "Fees unpaid"))
        r = self.client.post(f"{url}/release/")
        self.assertEqual((r.data["status"], r.data["withheld_reason"]), ("issued", ""))

    def test_cards_need_a_scheduled_exam(self):
        draft = self.make_exam("Draft", status="draft")
        self.assertError(self.client.post(f"{EXAMS}{draft.pk}/generate-admit-cards/", {}), 409, "not_scheduled")

    def test_one_section_at_a_time(self):
        self.assertEqual(self.generate(section=self.section_b.pk).data["created"], 2)
        self.assertEqual(self.generate().data, {"created": 2, "withheld": 0, "skipped": 2})

    def test_admit_card_data_has_papers_seat_and_instructions(self):
        room = create_room(self.campus, code="r1", name="Hall 1", capacity=10)
        self.client.post(f"{API}/exam-rooms/", {"exam": self.exam.pk, "room": room.pk})
        self.client.post(f"{EXAMS}{self.exam.pk}/seat-plan/", {})
        self.generate()
        card = AdmitCard.objects.get(student=self.ram)
        r = self.client.get(f"{API}/admit-cards/{card.pk}/data/")

        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([p["subject_name"] for p in r.data["papers"]], ["Physics", "Mathematics"])
        self.assertEqual(r.data["seat"]["room"], "Hall 1")
        self.assertEqual(r.data["exam"]["instructions"], "Bring your own calculator.")
        self.assertEqual(r.data["section"], "Grade 11 A")

    def test_a_student_sees_their_own_schedule_seat_and_card(self):
        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.generate()
        self.login(user)

        cards = self.client.get(f"{API}/admit-cards/me/").data
        self.assertEqual([c["student"]["name"] for c in cards], ["Ram Student"])
        exams = self.client.get(f"{EXAMS}me/").data
        self.assertEqual((exams[0]["name"], len(exams[0]["papers"]), exams[0]["admit_card"]["status"]),
                         ("First Terminal", 2, "issued"))

    def test_a_draft_exam_is_not_shown_to_students(self):
        self.make_exam("Draft", status="draft")
        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)
        self.assertEqual([e["name"] for e in self.client.get(f"{EXAMS}me/").data], ["First Terminal"])
