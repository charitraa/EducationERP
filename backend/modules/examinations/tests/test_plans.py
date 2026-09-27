"""Term results: several exams counted by weight."""
from datetime import timedelta
from decimal import Decimal as D

from tests.factories import create_program, create_student, create_user

from modules.students.services import place_student

from .. import results
from ..models import Exam, Result, ResultPlan
from .base import API, TODAY, ExamTestCase

PLANS = f"{API}/term-results/"


class PlanTestCase(ExamTestCase):
    def setUp(self):
        super().setUp()
        # Unit test (physics only, 20%), then the terminal (physics + math, 80%).
        self.unit = self.make_exam("Unit Test", papers=(("physics", 10),))
        self.term_exam = self.make_exam("Terminal", papers=(("physics", 3), ("math", 2)))
        self.split = lambda m: {self.section_a: {s: v for s, v in m.items() if s in (self.ram, self.shyam)},
                                self.section_b: {s: v for s, v in m.items() if s in (self.gita, self.binu)}}
        self.mark_all(self.unit, self.physics, self.split({self.ram: 50, self.shyam: 60, self.gita: 100, self.binu: 40}))
        self.finish_marking(self.term_exam, physics={self.ram: 80, self.shyam: 60, self.gita: 90, self.binu: 50},
                            math={self.ram: 70, self.shyam: 50, self.gita: 85, self.binu: 45})
        self.login(self.office)
        for exam in (self.unit, self.term_exam):
            self.client.post(f"{API}/exams/{exam.pk}/publish/")
        self.plan = self.make_plan()

    def make_plan(self, name="Term 1", weights=((20, "unit"), (80, "term_exam")), **extra):
        body = {"campus": self.campus.pk, "academic_year": self.year.pk, "program": self.program.pk, "name": name,
                "items": [{"exam": getattr(self, attr).pk, "weight": str(w)} for w, attr in weights], **extra}
        r = self.client.post(PLANS, body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return ResultPlan.objects.get(pk=r.data["id"])

    def of(self, student):
        return Result.objects.get(plan=self.plan, student=student)


class PlanSetupTests(PlanTestCase):
    def test_created_with_its_exams_and_scale(self):
        r = self.client.get(f"{PLANS}{self.plan.pk}/")
        self.assertEqual((r.data["status"], r.data["grade_scale"]), ("draft", self.scale.pk))
        self.assertEqual([i["exam_name"] for i in r.data["items"]], ["Unit Test", "Terminal"])
        self.assertEqual(D(r.data["weights_total"]), D("100"))

    def test_weights_cannot_exceed_100(self):
        body = {"campus": self.campus.pk, "academic_year": self.year.pk, "program": self.program.pk, "name": "Too much",
                "items": [{"exam": self.unit.pk, "weight": "60"}, {"exam": self.term_exam.pk, "weight": "60"}]}
        self.assertEqual(self.client.post(PLANS, body, format="json").status_code, 400)

    def test_an_exam_of_another_program_is_refused(self):
        other_program = create_program(self.org, code="school", name="School", first_level=1, last_level=10)
        exam = Exam.objects.create(organization=self.org, campus=self.campus, academic_year=self.year,
                                   exam_type=self.terminal, program=other_program, name="School exam",
                                   grade_scale=self.scale)
        body = {"campus": self.campus.pk, "academic_year": self.year.pk, "program": self.program.pk, "name": "Mixed",
                "items": [{"exam": exam.pk, "weight": "100"}]}
        self.assertEqual(self.client.post(PLANS, body, format="json").status_code, 400)

    def test_the_same_exam_twice_and_duplicate_names_are_refused(self):
        body = {"campus": self.campus.pk, "academic_year": self.year.pk, "program": self.program.pk, "name": "Twice",
                "items": [{"exam": self.unit.pk, "weight": "50"}, {"exam": self.unit.pk, "weight": "50"}]}
        self.assertEqual(self.client.post(PLANS, body, format="json").status_code, 400)
        body["name"], body["items"] = "Term 1", []
        self.assertEqual(self.client.post(PLANS, body, format="json").status_code, 400)

    def test_items_can_be_replaced_while_it_is_a_draft(self):
        r = self.client.patch(f"{PLANS}{self.plan.pk}/", {"items": [{"exam": self.term_exam.pk, "weight": "100"}]},
                              format="json")
        self.assertEqual((r.status_code, len(r.data["items"])), (200, 1))


class ComputeAndPublishTests(PlanTestCase):
    def test_weighted_subject_percentages(self):
        self.client.post(f"{PLANS}{self.plan.pk}/compute/")
        ram = self.of(self.ram)
        physics = ram.subjects.get(subject=self.physics)
        math = ram.subjects.get(subject=self.math)
        self.assertEqual(physics.percentage, D("74.00"))                # 20% of 50 + 80% of 80
        self.assertEqual(math.percentage, D("70.00"))                   # only the terminal had maths: weights fill
        self.assertEqual((ram.total_obtained, ram.total_full, ram.percentage), (D("144.00"), D("200.00"), D("72.00")))
        self.assertEqual((ram.grade_point, ram.letter, ram.status), (D("3.20"), "B+", "pass"))
        self.assertEqual([d["name"] for d in physics.detail], ["Unit Test", "Terminal"])
        self.assertEqual([d["weight"] for d in physics.detail], ["20.00", "80.00"])

    def test_ranks_and_fails_use_the_weighted_result(self):
        self.client.post(f"{PLANS}{self.plan.pk}/compute/")
        # Binu: physics 0.2*40 + 0.8*50 = 48 (pass), math 45 (pass) -> pass at 46.5%
        self.assertEqual(self.of(self.binu).status, "pass")
        self.assertEqual(self.of(self.gita).rank_in_level, 1)
        self.assertEqual([self.of(s).rank_in_section for s in (self.ram, self.shyam)], [1, 2])

    def test_a_student_absent_from_the_unit_test_gets_zero_at_its_weight(self):
        from ..models import Mark
        sheet = self.unit.subjects.get().sheets.get(section=self.section_a)
        mark = Mark.objects.get(sheet=sheet, enrollment=self.enrollment(self.ram, self.unit.subjects.get().date))
        self.login(self.office)
        self.client.patch(f"{API}/marks/{mark.pk}/", {"status": "absent", "reason": "Was ill"}, format="json")
        self.client.post(f"{PLANS}{self.plan.pk}/compute/")
        self.assertEqual(self.of(self.ram).subjects.get(subject=self.physics).percentage, D("64.00"))   # 0 + 64

    def test_a_student_who_joined_after_the_unit_test_is_graded_on_the_terminal_alone(self):
        late = create_student(self.campus, student_number="S-9", first_name="Late", admitted_on=TODAY - timedelta(days=5))
        place_student(student=late, section=self.section_a, on_date=TODAY - timedelta(days=5))
        self.finish_late(late)
        self.client.post(f"{PLANS}{self.plan.pk}/compute/")
        physics = self.of(late).subjects.get(subject=self.physics)
        self.assertEqual(physics.percentage, D("66.00"))                # terminal only: weights fill the gap

    def finish_late(self, late):
        from .. import services
        self.login(self.office)
        self.client.post(f"{API}/exams/{self.term_exam.pk}/unpublish/", {"reason": "Late student marks"})
        # (the exam was published before Late was placed; placing them earlier makes them expected)
        for subject, value in ((self.physics, 66), (self.math, 60)):
            paper = self.paper_of(self.term_exam, subject)
            sheet = paper.sheets.get(section=self.section_a)
            component = paper.components.get()
            entry = services.MarkEntry(self.enrollment(late, paper.date).pk, component.pk, "present", D(value))
            services.enter_marks(sheet=sheet, entries=[entry], by=self.office, reason="Admitted late, sat the paper")
        self.client.post(f"{API}/exams/{self.term_exam.pk}/publish/")

    def test_publishing_needs_weights_of_exactly_100(self):
        self.client.patch(f"{PLANS}{self.plan.pk}/", {"items": [{"exam": self.unit.pk, "weight": "20"},
                                                                {"exam": self.term_exam.pk, "weight": "70"}]}, format="json")
        self.assertError(self.client.post(f"{PLANS}{self.plan.pk}/publish/"), 400, "weights_not_100")

    def test_every_exam_must_be_published_first(self):
        self.client.post(f"{API}/exams/{self.unit.pk}/unpublish/", {"reason": "Recheck"})
        r = self.client.post(f"{PLANS}{self.plan.pk}/publish/")
        self.assertError(r, 409, "exams_not_published")
        self.assertEqual(r.data["error"]["details"]["exams"], ["Unit Test"])

    def test_publish_then_students_see_it_and_it_cannot_be_edited(self):
        r = self.client.post(f"{PLANS}{self.plan.pk}/publish/")
        self.assertEqual((r.status_code, r.data["results"]), (200, {"pass": 4}), r.data)

        self.assertError(self.client.patch(f"{PLANS}{self.plan.pk}/", {"name": "New"}), 409, "plan_published")
        self.assertError(self.client.delete(f"{PLANS}{self.plan.pk}/"), 409, "plan_published")
        self.assertError(self.client.post(f"{PLANS}{self.plan.pk}/compute/"), 409, "plan_published")

        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)
        cards = self.client.get(f"{API}/results/me/", {"include_card": "true"}).data
        term = next(c for c in cards if c["kind"] == "term")
        self.assertEqual((term["title"], term["percentage"]), ("Term 1", 72.0))

    def test_an_exam_used_by_a_published_term_result_cannot_be_unpublished(self):
        self.client.post(f"{PLANS}{self.plan.pk}/publish/")
        self.assertError(self.client.post(f"{API}/exams/{self.unit.pk}/unpublish/", {"reason": "x"}), 409, "used_by_plan")
        self.client.post(f"{PLANS}{self.plan.pk}/unpublish/", {"reason": "Rework"})
        self.assertEqual(self.client.post(f"{API}/exams/{self.unit.pk}/unpublish/", {"reason": "x"}).status_code, 200)

    def test_a_correction_in_an_exam_updates_the_published_term_result(self):
        self.client.post(f"{PLANS}{self.plan.pk}/publish/")
        self.assertEqual(self.of(self.ram).subjects.get(subject=self.physics).percentage, D("74.00"))

        sheet = self.paper_of(self.term_exam, self.physics).sheets.get(section=self.section_a)
        entry = {"enrollment": self.enrollment(self.ram, sheet.exam_subject.date).pk,
                 "component": sheet.exam_subject.components.get().pk, "status": "present", "marks": "90"}
        self.client.post(f"{API}/mark-sheets/{sheet.pk}/marks/", {"entries": [entry], "reason": "Recount"}, format="json")

        self.assertEqual(self.of(self.ram).subjects.get(subject=self.physics).percentage, D("82.00"))   # 10 + 72

    def test_plan_needs_exams(self):
        empty = self.make_plan("Empty", weights=())
        self.assertError(self.client.post(f"{PLANS}{empty.pk}/compute/"), 400, "no_exams")

