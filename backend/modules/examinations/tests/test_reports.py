"""Report cards, transcripts and the exam summary."""
from datetime import timedelta

from tests.factories import (
    create_attendance_record,
    create_attendance_session,
    create_parent,
    create_user,
)

from modules.parents.models import StudentParent

from ..models import Result
from .base import API, TODAY, ExamTestCase

EXAMS = f"{API}/exams/"
CARDS = f"{API}/report-cards/"
TRANSCRIPTS = f"{API}/transcripts/"


class PublishedExamTestCase(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.exam = self.make_exam(on_transcript=True)
        self.finish_marking(self.exam)
        self.login(self.office)
        self.client.post(f"{EXAMS}{self.exam.pk}/publish/")
        self.ram_user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = self.ram_user
        self.ram.save(update_fields=["user"])

    def result_of(self, student, exam=None):
        return Result.objects.get(exam=exam or self.exam, student=student)


class ReportCardTests(PublishedExamTestCase):
    def test_a_report_card_has_everything_to_print(self):
        self.login(self.office)
        r = self.client.get(f"{API}/results/{self.result_of(self.ram).pk}/report-card/")

        self.assertEqual(r.status_code, 200, r.data)
        card = r.data
        self.assertEqual((card["title"], card["kind"], card["academic_year"]), ("First Terminal", "exam", self.year.name))
        self.assertEqual((card["student"]["name"], card["student"]["student_number"]), ("Ram Student", "S-1"))
        self.assertEqual((card["campus"], card["program"], card["level_label"], card["section"]),
                         ("Lalitpur", "+2 Science", "Grade 11", "A"))
        self.assertEqual([s["subject_name"] for s in card["subjects"]], ["Mathematics", "Physics"])
        physics = next(s for s in card["subjects"] if s["subject_name"] == "Physics")
        self.assertEqual((physics["obtained"], physics["full"], physics["letter"], physics["grade_point"], physics["status"]),
                         (80.0, 100.0, "A", 3.6, "pass"))
        self.assertEqual((card["percentage"], card["grade_point"], card["letter"], card["result"]),
                         (75.0, 3.4, "B+", "pass"))
        self.assertEqual((card["rank_in_section"], card["rank_in_level"], card["class_size"]), (1, 2, 2))
        self.assertEqual(card["grading"][0], {"from": 90.0, "letter": "A+", "grade_point": 4.0,
                                              "remark": "Outstanding", "pass": True})

    def test_the_card_includes_attendance_up_to_the_exam(self):
        for i in range(4):
            session = create_attendance_session(self.section_a, day=TODAY - timedelta(days=30 + i))
            create_attendance_record(session, self.enrollment(self.ram, session.date), "present" if i < 3 else "absent")
        self.login(self.office)
        card = self.client.get(f"{API}/results/{self.result_of(self.ram).pk}/report-card/").data
        self.assertEqual((card["attendance"]["total"], card["attendance"]["attended"], card["attendance"]["percentage"]),
                         (4, 3, 75.0))

    def test_a_failed_student_has_no_rank_and_a_fail_status(self):
        self.login(self.office)
        card = self.client.get(f"{API}/results/{self.result_of(self.binu).pk}/report-card/").data
        self.assertEqual((card["result"], card["rank_in_section"], card["division"]), ("fail", None, ""))

    def test_a_class_of_cards_for_printing(self):
        self.login(self.office)
        r = self.client.get(CARDS, {"exam": self.exam.pk, "section": self.section_a.pk})
        self.assertEqual([c["student"]["name"] for c in r.data], ["Ram Student", "Shyam Student"])

    def test_one_students_card(self):
        self.login(self.office)
        r = self.client.get(CARDS, {"exam": self.exam.pk, "student": self.gita.pk})
        self.assertEqual([c["student"]["name"] for c in r.data], ["Gita Student"])

    def test_the_request_must_name_a_source_and_a_class_or_student(self):
        self.login(self.office)
        self.assertEqual(self.client.get(CARDS, {"section": self.section_a.pk}).status_code, 400)
        self.assertEqual(self.client.get(CARDS, {"exam": self.exam.pk}).status_code, 400)
        self.assertEqual(self.client.get(CARDS, {"exam": self.exam.pk, "plan": 1, "section": 1}).status_code, 400)

    def test_teachers_cannot_pull_report_cards(self):
        self.login(self.hari)
        self.assertEqual(self.client.get(CARDS, {"exam": self.exam.pk, "section": self.section_a.pk}).status_code, 403)

    def test_a_student_reads_their_own_card_and_a_parent_their_childs(self):
        self.login(self.ram_user)
        r = self.client.get(f"{CARDS}me/")
        self.assertEqual((r.status_code, r.data["student"]["name"]), (200, "Ram Student"))

        parent_user = create_user(self.org, email="parent@kmc.test")
        parent = create_parent(self.org, first_name="Pita", user=parent_user)
        StudentParent.objects.create(organization=self.org, parent=parent, student=self.ram, relationship="father")
        self.login(parent_user)
        self.assertEqual(self.client.get(f"{CARDS}me/").data["student"]["name"], "Ram Student")
        self.assertEqual(self.client.get(f"{CARDS}me/", {"student": self.gita.pk}).status_code, 404)

    def test_the_class_teachers_remark_appears_on_the_card(self):
        self.section_a.class_teacher = self.hari
        self.section_a.save(update_fields=["class_teacher"])
        self.login(self.hari)
        result = self.result_of(self.ram)
        r = self.client.post(f"{API}/results/{result.pk}/remark/", {"remark": "Works hard. Keep it up."})
        self.assertEqual(r.status_code, 200, r.data)

        self.login(self.ram_user)
        self.assertEqual(self.client.get(f"{CARDS}me/").data["remark"], "Works hard. Keep it up.")

    def test_only_the_class_teacher_or_office_can_write_remarks(self):
        self.section_a.class_teacher = self.hari
        self.section_a.save(update_fields=["class_teacher"])
        result = self.result_of(self.ram)
        self.login(self.sita)
        self.assertEqual(self.client.post(f"{API}/results/{result.pk}/remark/", {"remark": "x"}).status_code, 403)
        self.login(self.office)
        self.assertEqual(self.client.post(f"{API}/results/{result.pk}/remark/", {"remark": "Good"}).status_code, 200)


class TranscriptTests(PublishedExamTestCase):
    def second_exam(self, physics_marks=None, **fields):
        exam = self.make_exam("Final", papers=(("physics", 1),), on_transcript=True, **fields)
        marks = physics_marks or {self.ram: 92, self.shyam: 60, self.gita: 90, self.binu: 30}
        self.mark_all(exam, self.physics, {
            self.section_a: {s: m for s, m in marks.items() if s in (self.ram, self.shyam)},
            self.section_b: {s: m for s, m in marks.items() if s in (self.gita, self.binu)}})
        self.login(self.office)
        self.client.post(f"{EXAMS}{exam.pk}/publish/")
        return exam

    def transcript(self, student):
        self.login(self.office)
        return self.client.get(f"{TRANSCRIPTS}{student.pk}/")

    def test_a_transcript_lists_published_results_with_a_cumulative_gpa(self):
        r = self.transcript(self.ram)

        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([x["title"] for x in r.data["records"]], ["First Terminal"])
        self.assertEqual(r.data["cumulative"], {"credits": 8.0, "credits_passed": 8.0, "gpa": 3.4})

    def test_credits_and_gpa_accumulate_across_results(self):
        self.second_exam()                                 # physics 92%: 4.0 at 4 credits
        r = self.transcript(self.ram)
        self.assertEqual([x["title"] for x in r.data["records"]], ["First Terminal", "Final"])
        self.assertEqual(r.data["cumulative"]["credits"], 12.0)
        self.assertEqual(r.data["cumulative"]["gpa"], 3.6)                       # (14.4 + 12.8 + 16) / 12

    def test_failed_credits_count_towards_the_gpa_but_are_not_earned(self):
        r = self.transcript(self.binu)                    # physics 30 (NG, 0.0), math 40 (2.0)
        self.assertEqual(r.data["cumulative"], {"credits": 8.0, "credits_passed": 4.0, "gpa": 1.0})

    def test_exams_not_marked_for_the_transcript_are_left_out(self):
        exam = self.make_exam("Class test", papers=(("physics", 7),), on_transcript=False)
        self.mark_all(exam, self.physics, {self.section_a: {self.ram: 50, self.shyam: 50},
                                           self.section_b: {self.gita: 50, self.binu: 50}})
        self.login(self.office)
        self.client.post(f"{EXAMS}{exam.pk}/publish/")
        self.assertEqual([x["title"] for x in self.transcript(self.ram).data["records"]], ["First Terminal"])

    def test_unpublished_results_are_not_on_it(self):
        self.client.post(f"{EXAMS}{self.exam.pk}/unpublish/", {"reason": "Recheck"})
        r = self.transcript(self.ram)
        self.assertEqual((r.data["records"], r.data["cumulative"]["gpa"]), ([], None))

    def test_students_and_parents_read_their_own(self):
        self.login(self.ram_user)
        self.assertEqual(self.client.get(f"{TRANSCRIPTS}me/").data["student"]["name"], "Ram Student")
        self.assertEqual(self.client.get(f"{TRANSCRIPTS}{self.gita.pk}/").status_code, 403)     # staff endpoint

    def test_a_term_result_appears_once_published(self):
        plan = self.client.post(f"{API}/term-results/", {
            "campus": self.campus.pk, "academic_year": self.year.pk, "program": self.program.pk, "name": "Term 1",
            "items": [{"exam": self.exam.pk, "weight": "100"}]}, format="json")
        self.login(self.office)
        self.client.post(f"{API}/term-results/{plan.data['id']}/publish/")
        titles = [x["title"] for x in self.transcript(self.ram).data["records"]]
        self.assertEqual(titles, ["First Terminal", "Term 1"])


class SummaryTests(PublishedExamTestCase):
    def test_pass_rates_averages_and_toppers(self):
        self.login(self.office)
        r = self.client.get(f"{EXAMS}{self.exam.pk}/summary/")

        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["results"], r.data["by_status"]), (4, {"pass": 3, "fail": 1}))
        self.assertEqual(r.data["pass_rate"], 75.0)
        self.assertEqual([t["student_name"] for t in r.data["toppers"]], ["Gita Student", "Ram Student", "Shyam Student"])
        physics = next(s for s in r.data["subjects"] if s["subject_name"] == "Physics")
        self.assertEqual((physics["students"], physics["passed"], physics["pass_rate"], physics["highest"], physics["lowest"]),
                         (4, 3, 75.0, 90.0, 30.0))
        self.assertEqual(physics["average"], 65.0)
