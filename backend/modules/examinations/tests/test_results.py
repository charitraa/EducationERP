"""From marks to results: computing, publishing, visibility, corrections."""
from decimal import Decimal as D

from core.audit.models import AuditLog
from tests.factories import create_parent, create_student, user_with_system_role, create_user, grant, create_role
from modules.parents.models import StudentParent
from modules.students.services import place_student

from .. import results, services
from ..models import MarkSheet, Result
from .base import API, TODAY, ExamTestCase

EXAMS = f"{API}/exams/"
RESULTS = f"{API}/results/"


class ResultTestCase(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.exam = self.make_exam()                       # physics + math, 100 marks each, pass 35

    def result_of(self, student, exam=None):
        return Result.objects.get(exam=exam or self.exam, student=student)

    def line(self, result, subject):
        return result.subjects.get(subject=subject)

    def publish(self, exam=None):
        self.login(self.office)
        return self.client.post(f"{EXAMS}{(exam or self.exam).pk}/publish/")


class ComputeTests(ResultTestCase):
    def setUp(self):
        super().setUp()
        self.finish_marking(self.exam)
        results.compute_exam_results(self.exam)

    def test_totals_percentage_gpa_and_letter(self):
        ram = self.result_of(self.ram)                      # 80 + 70 of 200
        self.assertEqual((ram.total_obtained, ram.total_full, ram.percentage), (D("150"), D("200"), D("75.00")))
        self.assertEqual((ram.grade_point, ram.letter, ram.status), (D("3.40"), "B+", "pass"))

    def test_each_subject_has_its_own_grade(self):
        ram = self.result_of(self.ram)
        physics, math = self.line(ram, self.physics), self.line(ram, self.math)
        self.assertEqual((physics.percentage, physics.letter, physics.grade_point), (D("80.00"), "A", D("3.60")))
        self.assertEqual((math.percentage, math.letter, math.grade_point), (D("70.00"), "B+", D("3.20")))
        self.assertEqual(physics.credit_hours, D("4"))
        self.assertEqual(physics.detail[0]["marks"], "80.00")            # the component that made it

    def test_one_failed_subject_fails_the_result(self):
        binu = self.result_of(self.binu)                    # physics 30 -> NG, math 40
        self.assertEqual(binu.status, "fail")
        self.assertEqual((self.line(binu, self.physics).status, self.line(binu, self.physics).letter), ("fail", "NG"))
        self.assertEqual(self.line(binu, self.math).status, "pass")

    def test_ranks_go_to_passing_students_only(self):
        self.assertEqual((self.result_of(self.ram).rank_in_section, self.result_of(self.shyam).rank_in_section), (1, 2))
        self.assertEqual(self.result_of(self.gita).rank_in_section, 1)
        self.assertIsNone(self.result_of(self.binu).rank_in_section)
        level = {s: self.result_of(s).rank_in_level for s in (self.gita, self.ram, self.shyam, self.binu)}
        self.assertEqual(level, {self.gita: 1, self.ram: 2, self.shyam: 3, self.binu: None})

    def test_recomputing_gives_the_same_result_and_no_duplicates(self):
        before = list(Result.objects.order_by("pk").values_list("pk", "percentage", "rank_in_level"))
        results.compute_exam_results(self.exam)
        self.assertEqual(before, list(Result.objects.order_by("pk").values_list("pk", "percentage", "rank_in_level")))
        self.assertEqual(Result.objects.filter(exam=self.exam).count(), 4)

    def test_a_tie_shares_a_rank(self):
        # Gita and Ram both on 85%: rank 1 in the level, Shyam third.
        exam = self.make_exam("Ties", papers=(("physics", 6),))
        same = {self.ram: 85, self.shyam: 60, self.gita: 85, self.binu: 40}
        self.mark_all(exam, self.physics, {self.section_a: {s: m for s, m in same.items() if s in (self.ram, self.shyam)},
                                           self.section_b: {s: m for s, m in same.items() if s in (self.gita, self.binu)}})
        results.compute_exam_results(exam)
        self.assertEqual([self.result_of(s, exam).rank_in_level for s in (self.ram, self.gita, self.shyam)], [1, 1, 3])


class SpecialCaseTests(ResultTestCase):
    def test_a_student_absent_from_a_paper_fails_it_with_zero(self):
        self.finish_marking(self.exam, physics={self.ram: "absent", self.shyam: 60, self.gita: 90, self.binu: 50})
        results.compute_exam_results(self.exam)
        line = self.line(self.result_of(self.ram), self.physics)
        self.assertEqual((line.absent, line.status, line.percentage), (True, "fail", D("0.00")))
        self.assertEqual(self.result_of(self.ram).status, "fail")

    def test_an_exempt_paper_is_left_out_of_the_result(self):
        self.finish_marking(self.exam, physics={self.ram: "exempt", self.shyam: 60, self.gita: 90, self.binu: 50})
        results.compute_exam_results(self.exam)
        ram = self.result_of(self.ram)
        self.assertEqual((ram.total_full, ram.status), (D("100"), "pass"))     # math only
        self.assertFalse(ram.subjects.filter(subject=self.physics).exists())

    def test_a_withheld_paper_withholds_the_result_but_not_the_others(self):
        self.finish_marking(self.exam, physics={self.ram: "withheld", self.shyam: 60, self.gita: 90, self.binu: 50})
        results.compute_exam_results(self.exam)
        self.assertEqual(self.result_of(self.ram).status, "withheld")
        self.assertEqual(self.result_of(self.shyam).status, "pass")

    def test_a_missing_sheet_makes_incomplete_results(self):
        self.mark_all(self.exam, self.physics, {self.section_a: {self.ram: 70, self.shyam: 50}})
        counts = results.compute_exam_results(self.exam)
        self.assertEqual(counts["incomplete"], 4)          # math isn't marked; section B has nothing at all

    def test_an_elective_paper_only_counts_for_those_who_take_it(self):
        exam = self.make_exam("With computer", papers=(("physics", 6), ("computer", 5)))
        self.mark_all(exam, self.physics, {self.section_a: {self.ram: 70, self.shyam: 50},
                                           self.section_b: {self.gita: 90, self.binu: 60}})
        self.mark_all(exam, self.computer, {self.section_a: {self.ram: 80},
                                            self.section_b: {self.gita: 85, self.binu: 55}})
        results.compute_exam_results(exam)

        self.assertEqual(self.result_of(self.ram, exam).subjects.count(), 2)
        self.assertEqual(self.result_of(self.shyam, exam).subjects.count(), 1)     # takes biology, not computer
        self.assertEqual(self.result_of(self.shyam, exam).status, "pass")

    def test_a_student_placed_after_the_exam_started_gets_no_result(self):
        self.finish_marking(self.exam)
        late = create_student(self.campus, student_number="S-9", first_name="Late", admitted_on=TODAY)
        place_student(student=late, section=self.section_a)
        results.compute_exam_results(self.exam)
        self.assertFalse(Result.objects.filter(student=late).exists())

    def test_two_component_paper_with_a_failed_practical(self):
        from ..models import ExamComponent
        exam = self.make_exam("Practical", papers=(), status="draft")
        paper = self.paper(exam, self.physics, 4, full=75, pass_marks=27)
        practical = ExamComponent.objects.create(organization=self.org, exam_subject=paper, kind="practical",
                                                 name="Practical", full_marks=25, pass_marks=10, order=1)
        services.schedule(exam)
        theory = paper.components.get(name="Theory")
        for section, students in ((self.section_a, (self.ram, self.shyam)), (self.section_b, (self.gita, self.binu))):
            sheet, _ = services.open_sheet(paper, section, by=self.office)
            entries = []
            for s in students:
                e = self.enrollment(s, paper.date)
                entries += [services.MarkEntry(e.pk, theory.pk, "present", D("70")),
                            services.MarkEntry(e.pk, practical.pk, "present", D("9") if s == self.ram else D("20"))]
            services.enter_marks(sheet=sheet, entries=entries, by=self.office)
            services.submit_sheet(sheet, by=self.office)
            services.verify_sheet(sheet, by=self.office)
        results.compute_exam_results(exam)

        ram = self.line(self.result_of(self.ram, exam), self.physics)
        self.assertEqual((ram.percentage, ram.status), (D("79.00"), "fail"))       # 79% but practical under 10
        self.assertEqual(self.line(self.result_of(self.shyam, exam), self.physics).status, "pass")


class PublishTests(ResultTestCase):
    def test_publishing_says_what_is_missing(self):
        self.login(self.office)
        r = self.client.post(f"{EXAMS}{self.exam.pk}/publish/")
        self.assertError(r, 409, "not_ready")
        state = r.data["error"]["details"]
        self.assertEqual((state["expected_sheets"], len(state["not_started"])), (4, 4))

        self.mark_all(self.exam, self.physics, {self.section_a: {self.ram: 70, self.shyam: 50}}, verify=False)
        state = self.client.get(f"{EXAMS}{self.exam.pk}/readiness/").data
        self.assertEqual((len(state["not_started"]), len(state["not_verified"]), state["ready"]), (3, 1, False))
        self.assertEqual(state["not_verified"][0]["status"], "submitted")

    def test_publish_when_every_sheet_is_verified(self):
        self.finish_marking(self.exam)
        r = self.publish()

        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["results"], {"pass": 3, "fail": 1})
        self.exam.refresh_from_db()
        self.assertEqual((self.exam.status, self.exam.published_by), ("published", self.office))
        self.assertTrue(AuditLog.objects.filter(module="examinations", object_id=str(self.exam.pk),
                                                metadata__has_key="results").exists())

    def test_publishing_twice_or_before_scheduling_is_refused(self):
        self.finish_marking(self.exam)
        self.publish()
        self.assertError(self.publish(), 409, "already_published")
        draft = self.make_exam("Draft", status="draft")
        self.assertError(self.publish(draft), 409, "not_scheduled")

    def test_a_student_enrolled_back_in_time_blocks_publishing_until_marked(self):
        # Every sheet was verified, then the office admits a student with an earlier start date.
        self.finish_marking(self.exam)
        late = self.student("S-9", "Late", self.section_a)
        self.login(self.office)
        r = self.client.post(f"{EXAMS}{self.exam.pk}/publish/")
        self.assertError(r, 409, "incomplete_marks")
        self.assertEqual(r.data["error"]["details"]["results"], {"pass": 3, "fail": 1, "incomplete": 1})

        # The office marks them on the verified sheets, with a reason, and publishes.
        for subject in (self.physics, self.math):
            sheet = MarkSheet.objects.get(exam_subject=self.paper_of(self.exam, subject), section=self.section_a)
            component = sheet.exam_subject.components.get()
            entry = services.MarkEntry(self.enrollment(late, sheet.exam_subject.date).pk, component.pk, "present", D("55"))
            services.enter_marks(sheet=sheet, entries=[entry], by=self.office, reason="Admitted late, sat the paper")
        self.assertEqual(self.client.post(f"{EXAMS}{self.exam.pk}/publish/").status_code, 200)

    def test_a_student_who_joins_after_the_papers_is_simply_not_expected(self):
        self.finish_marking(self.exam)
        joined = create_student(self.campus, student_number="S-8", first_name="New", admitted_on=TODAY)
        place_student(student=joined, section=self.section_a)
        self.assertEqual(self.publish().status_code, 200)
        self.assertFalse(Result.objects.filter(student=joined).exists())

    def test_only_publishers_can_publish(self):
        self.finish_marking(self.exam)
        self.login(self.hari)
        self.assertEqual(self.client.post(f"{EXAMS}{self.exam.pk}/publish/").status_code, 403)

    def test_sheets_are_locked_for_sending_back_once_published(self):
        self.finish_marking(self.exam)
        self.publish()
        sheet = MarkSheet.objects.first()
        self.assertError(self.client.post(f"{API}/mark-sheets/{sheet.pk}/send-back/", {"reason": "x"}), 409,
                         "exam_published")

    def test_compute_previews_results_without_publishing(self):
        self.finish_marking(self.exam)
        self.login(self.office)
        r = self.client.post(f"{EXAMS}{self.exam.pk}/compute/")
        self.assertEqual((r.status_code, r.data["results"]), (200, {"pass": 3, "fail": 1}))
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.status, "scheduled")


class VisibilityTests(ResultTestCase):
    def setUp(self):
        super().setUp()
        self.finish_marking(self.exam)
        self.ram_user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = self.ram_user
        self.ram.save(update_fields=["user"])
        self.gita_user = create_user(self.org, email="gita@kmc.test")
        self.gita.user = self.gita_user
        self.gita.save(update_fields=["user"])
        self.login(self.office)
        self.client.post(f"{EXAMS}{self.exam.pk}/compute/")

    def test_staff_see_computed_results_before_publishing(self):
        r = self.client.get(RESULTS, {"exam": self.exam.pk})
        self.assertEqual((r.status_code, r.data["count"]), (200, 4))
        self.assertNotIn("subjects", r.data["results"][0])              # the list is light
        detail = self.client.get(f"{RESULTS}{self.result_of(self.ram).pk}/")
        self.assertEqual(len(detail.data["subjects"]), 2)
        self.assertFalse(detail.data["published"])

    def test_a_student_sees_nothing_until_it_is_published(self):
        self.login(self.ram_user)
        self.assertEqual(self.client.get(f"{RESULTS}me/").data, [])
        self.assertEqual(self.client.get(f"{API}/report-cards/me/").status_code, 404)

        self.publish()
        self.login(self.ram_user)
        r = self.client.get(f"{RESULTS}me/")
        self.assertEqual([x["student_name"] for x in r.data], ["Ram Student"])       # only their own
        self.assertEqual(r.data[0]["letter"], "B+")
        self.assertEqual(self.client.get(f"{API}/report-cards/me/").status_code, 200)

    def test_unpublishing_hides_it_again(self):
        self.publish()
        self.client.post(f"{EXAMS}{self.exam.pk}/unpublish/", {"reason": "Wrong paper marked"})
        self.login(self.ram_user)
        self.assertEqual(self.client.get(f"{RESULTS}me/").data, [])

    def test_a_parent_sees_only_their_childs_results(self):
        parent_user = create_user(self.org, email="parent@kmc.test")
        parent = create_parent(self.org, first_name="Pita", user=parent_user)
        StudentParent.objects.create(organization=self.org, parent=parent, student=self.ram, relationship="father")
        self.publish()

        self.login(parent_user)
        self.assertEqual(self.client.get(f"{RESULTS}me/").data[0]["student_name"], "Ram Student")
        self.assertEqual(self.client.get(f"{RESULTS}me/", {"student": self.gita.pk}).status_code, 404)

    def test_a_student_cannot_use_the_staff_endpoints(self):
        self.publish()
        self.login(self.ram_user)
        self.assertEqual(self.client.get(RESULTS).status_code, 403)
        self.assertEqual(self.client.get(f"{RESULTS}{self.result_of(self.gita).pk}/").status_code, 403)

    def test_unpublishing_needs_a_reason_and_a_published_exam(self):
        self.assertError(self.client.post(f"{EXAMS}{self.exam.pk}/unpublish/", {"reason": "x"}), 409, "not_published")
        self.publish()
        self.assertEqual(self.client.post(f"{EXAMS}{self.exam.pk}/unpublish/", {}).status_code, 400)
        r = self.client.post(f"{EXAMS}{self.exam.pk}/unpublish/", {"reason": "Recheck"})
        self.assertEqual((r.status_code, r.data["status"]), (200, "scheduled"))
        self.assertEqual(Result.objects.filter(exam=self.exam).count(), 4)       # kept for staff


class CorrectionAfterPublishTests(ResultTestCase):
    def setUp(self):
        super().setUp()
        self.finish_marking(self.exam)
        self.publish()
        sheet = MarkSheet.objects.get(exam_subject=self.paper_of(self.exam, self.physics), section=self.section_b)
        self.sheet = sheet
        self.component = sheet.exam_subject.components.get()

    def correct_binu(self, marks, reason="Recount"):
        self.login(self.office)
        entry = {"enrollment": self.enrollment(self.binu, self.sheet.exam_subject.date).pk,
                 "component": self.component.pk, "status": "present", "marks": str(marks)}
        return self.client.post(f"{API}/mark-sheets/{self.sheet.pk}/marks/", {"entries": [entry], "reason": reason},
                                format="json")

    def test_a_correction_recomputes_the_result_and_ranks(self):
        self.assertEqual(self.result_of(self.binu).status, "fail")
        r = self.correct_binu(45)

        self.assertEqual(r.status_code, 200, r.data)
        binu = self.result_of(self.binu)
        self.assertEqual((binu.status, binu.percentage, binu.rank_in_section), ("pass", D("42.50"), 2))
        self.assertEqual(self.result_of(self.shyam).rank_in_level, 3)              # 55% (Shyam) vs Binu 42.5
        self.assertEqual(self.result_of(self.binu).rank_in_level, 4)

    def test_a_correction_that_moves_a_rank_updates_everyone_affected(self):
        self.correct_binu(100)                                # 100 + 40 = 70%: now ahead of Ram (75%)? no: behind Gita
        binu = self.result_of(self.binu)
        self.assertEqual(binu.percentage, D("70.00"))
        self.assertEqual(self.result_of(self.gita).rank_in_section, 1)
        self.assertEqual(binu.rank_in_section, 2)

    def test_it_stays_visible_and_is_recorded(self):
        self.correct_binu(45, "Marks were added wrongly")
        self.assertTrue(self.result_of(self.binu).is_published)
        self.assertTrue(AuditLog.objects.filter(module="examinations", metadata__recomputed_after_correction=1).exists())

    def test_a_teacher_cannot_change_published_marks(self):
        self.login(self.hari)
        entry = {"enrollment": self.enrollment(self.binu, self.sheet.exam_subject.date).pk,
                 "component": self.component.pk, "status": "present", "marks": "99"}
        r = self.client.post(f"{API}/mark-sheets/{self.sheet.pk}/marks/", {"entries": [entry], "reason": "x"},
                             format="json")
        self.assertError(r, 409, "sheet_locked")
