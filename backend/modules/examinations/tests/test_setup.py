"""Building an exam: types, papers, clashes, scheduling, calendar."""
from datetime import timedelta
from decimal import Decimal

from modules.academics.models import CalendarEvent
from tests.factories import create_calendar_event

from ..models import ExamSubject
from .base import API, TODAY, ExamTestCase

EXAMS = f"{API}/exams/"
PAPERS = f"{API}/exam-subjects/"
D = lambda days: (TODAY - timedelta(days=days)).isoformat()


class ExamTypeTests(ExamTestCase):
    def test_crud_and_unique_code(self):
        self.login(self.office)
        r = self.client.post(f"{API}/exam-types/", {"code": "Unit-Test", "name": "Unit test"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["code"], "unit-test")                    # codes are lowercase
        self.assertEqual(self.client.post(f"{API}/exam-types/", {"code": "unit-test", "name": "Dup"}).status_code, 400)

    def test_cannot_delete_a_type_that_exams_use(self):
        self.login(self.office)
        self.make_exam(status="draft")
        self.assertError(self.client.delete(f"{API}/exam-types/{self.terminal.pk}/"), 409, "in_use")


class CreateExamTests(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def body(self, **extra):
        return {"campus": self.campus.pk, "academic_year": self.year.pk, "exam_type": self.terminal.pk,
                "program": self.program.pk, "name": "First Terminal", **extra}

    def test_create_picks_the_grade_scale(self):
        r = self.client.post(EXAMS, self.body())

        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["status"], r.data["grade_scale"]), ("draft", self.scale.pk))

    def test_a_program_scale_is_preferred(self):
        own = self.make_scale(program=self.program, preset="percentage")
        self.assertEqual(self.client.post(EXAMS, self.body()).data["grade_scale"], own.pk)

    def test_without_any_scale_it_says_so(self):
        self.scale.delete()
        self.assertError(self.client.post(EXAMS, self.body()), 400, "no_grade_scale")

    def test_a_term_must_belong_to_the_year(self):
        from tests.factories import create_academic_year, create_term
        other_year = create_academic_year(self.org, name="2081/82", start=TODAY - timedelta(days=900),
                                          end=TODAY - timedelta(days=500))
        term = create_term(other_year)
        self.assertEqual(self.client.post(EXAMS, self.body(term=term.pk)).status_code, 400)

    def test_duplicate_name_in_the_same_year_and_campus(self):
        self.client.post(EXAMS, self.body())
        self.assertEqual(self.client.post(EXAMS, self.body()).status_code, 400)

    def test_scale_of_another_program_is_refused(self):
        from tests.factories import create_program
        other = create_program(self.org, code="school", name="School", first_level=1, last_level=10)
        scale = self.make_scale(program=other, preset="percentage")
        self.assertEqual(self.client.post(EXAMS, self.body(grade_scale=scale.pk)).status_code, 400)

    def test_other_organizations_ids_look_missing(self):
        from tests.factories import create_campus, create_organization
        other = create_campus(create_organization(code="other"), code="x")
        self.assertEqual(self.client.post(EXAMS, self.body(campus=other.pk)).status_code, 400)

    def test_only_a_draft_can_be_deleted(self):
        exam = self.make_exam()
        self.assertError(self.client.delete(f"{EXAMS}{exam.pk}/"), 409, "not_draft")
        draft = self.make_exam("Draft", status="draft")
        self.assertEqual(self.client.delete(f"{EXAMS}{draft.pk}/").status_code, 204)


class PaperTests(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)
        self.exam = self.make_exam(status="draft", papers=())

    def body(self, subject=None, **extra):
        return {"exam": self.exam.pk, "subject": (subject or self.physics).pk, "level": 11, "date": D(3),
                "start_time": "10:00", "end_time": "12:00",
                "components": [{"kind": "theory", "name": "Theory", "full_marks": "75", "pass_marks": "27"},
                               {"kind": "practical", "name": "Practical", "full_marks": "25", "pass_marks": "10"}],
                **extra}

    def test_create_with_components(self):
        r = self.client.post(PAPERS, self.body())

        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Decimal(r.data["full_marks"]), Decimal("100"))
        self.assertEqual([c["name"] for c in r.data["components"]], ["Theory", "Practical"])

    def test_components_are_required_and_checked(self):
        self.assertEqual(self.client.post(PAPERS, self.body(components=[])).status_code, 400)
        bad_pass = [{"kind": "theory", "name": "T", "full_marks": "50", "pass_marks": "60"}]
        self.assertEqual(self.client.post(PAPERS, self.body(components=bad_pass)).status_code, 400)
        twice = [{"kind": "theory", "name": "T", "full_marks": "50", "pass_marks": "10"}] * 2
        self.assertEqual(self.client.post(PAPERS, self.body(components=twice)).status_code, 400)

    def test_the_subject_must_be_taught_at_that_level(self):
        from tests.factories import create_subject
        art = create_subject(self.org, code="art", name="Art")
        self.assertEqual(self.client.post(PAPERS, self.body(art)).status_code, 400)

    def test_one_paper_per_subject_and_level(self):
        self.client.post(PAPERS, self.body())
        self.assertEqual(self.client.post(PAPERS, self.body()).status_code, 400)

    def test_the_level_must_exist_in_the_program(self):
        self.assertEqual(self.client.post(PAPERS, self.body(level=3)).status_code, 400)

    def test_end_must_be_after_start(self):
        self.assertEqual(self.client.post(PAPERS, self.body(start_time="12:00", end_time="10:00")).status_code, 400)

    def test_date_must_be_in_the_academic_year(self):
        self.assertError(self.client.post(PAPERS, self.body(date=D(400))), 400, "outside_year")

    def test_a_holiday_is_refused_but_an_event_is_not(self):
        create_calendar_event(self.org, title="Dashain", day=TODAY - timedelta(days=3), kind="holiday",
                              suspends_classes=True)
        self.assertError(self.client.post(PAPERS, self.body()), 400, "on_holiday")
        create_calendar_event(self.org, title="Sports", day=TODAY - timedelta(days=4), kind="event")
        self.assertEqual(self.client.post(PAPERS, self.body(date=D(4))).status_code, 201)

    def test_a_holiday_for_another_campus_or_program_doesnt_matter(self):
        create_calendar_event(self.org, title="Bhaktapur only", day=TODAY - timedelta(days=3), kind="holiday",
                              suspends_classes=True, campus=self.other_campus)
        self.assertEqual(self.client.post(PAPERS, self.body()).status_code, 201)

    def test_two_papers_for_the_same_students_cannot_overlap(self):
        self.client.post(PAPERS, self.body())
        clash = self.client.post(PAPERS, self.body(self.math, start_time="11:00", end_time="13:00"))
        self.assertError(clash, 409, "paper_clash")
        later = self.client.post(PAPERS, self.body(self.math, start_time="12:00", end_time="14:00"))
        self.assertEqual(later.status_code, 201, later.data)             # back to back is fine

    def test_two_electives_nobody_takes_together_can_share_a_slot(self):
        # Ram takes computer, Shyam takes biology: no one takes both.
        self.assertEqual(self.client.post(PAPERS, self.body(self.computer)).status_code, 201)
        r = self.client.post(PAPERS, self.body(self.biology))
        self.assertEqual(r.status_code, 201, r.data)

    def test_two_electives_someone_takes_together_clash(self):
        from tests.factories import create_student_elective
        create_student_elective(self.enrollment(self.ram), self.biology)          # Ram now takes both
        self.client.post(PAPERS, self.body(self.computer))
        self.assertError(self.client.post(PAPERS, self.body(self.biology)), 409, "paper_clash")

    def test_an_elective_clashes_with_a_compulsory_paper(self):
        self.client.post(PAPERS, self.body(self.physics))
        self.assertError(self.client.post(PAPERS, self.body(self.computer)), 409, "paper_clash")

    def test_add_curriculum_makes_a_paper_per_subject(self):
        r = self.client.post(f"{EXAMS}{self.exam.pk}/add-curriculum/", {"levels": [11], "full_marks": "100",
                                                                        "pass_marks": "35"})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(len(r.data), 4)
        again = self.client.post(f"{EXAMS}{self.exam.pk}/add-curriculum/", {"levels": [11]})
        self.assertEqual(len(again.data), 0)                             # nothing new
        self.assertEqual(self.client.post(f"{EXAMS}{self.exam.pk}/add-curriculum/", {"levels": [3]}).status_code, 400)

    def test_changing_components_after_marks_is_refused(self):
        exam = self.make_exam("Marked")
        self.fill(self.sheet(exam, self.physics, self.section_a), {self.ram: 70, self.shyam: 50})
        paper = self.paper_of(exam, self.physics)
        r = self.client.patch(f"{PAPERS}{paper.pk}/", {"components": [
            {"kind": "theory", "name": "Theory", "full_marks": "50", "pass_marks": "20"}]}, format="json")
        self.assertError(r, 409, "marks_entered")
        self.assertError(self.client.delete(f"{PAPERS}{paper.pk}/"), 409, "marks_entered")

    def test_moving_a_paper_after_scheduling_keeps_the_exam_span(self):
        exam = self.make_exam("Scheduled")
        paper = self.paper_of(exam, self.math)
        self.client.patch(f"{PAPERS}{paper.pk}/", {"date": D(6)})
        exam.refresh_from_db()
        self.assertEqual(exam.start_date, TODAY - timedelta(days=6))
        event = CalendarEvent.objects.get(pk=exam.calendar_event_id)
        self.assertEqual(event.start_date, exam.start_date)


class ScheduleTests(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.office)

    def test_schedule_sets_the_span_and_the_calendar(self):
        exam = self.make_exam(status="draft")
        r = self.client.post(f"{EXAMS}{exam.pk}/schedule/")

        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "scheduled")
        self.assertEqual((r.data["start_date"], r.data["end_date"]), (D(3), D(2)))
        event = CalendarEvent.objects.get(kind="exam")
        self.assertEqual((event.title, event.program, event.level, event.suspends_classes),
                         (exam.name, self.program, 11, True))

    def test_an_exam_day_stops_lessons_for_that_program(self):
        from modules.academics.selectors import closure_on
        exam = self.make_exam()
        self.assertIsNotNone(closure_on(self.section_a, exam.start_date))
        self.assertIsNone(closure_on(self.section_a, exam.start_date - timedelta(days=5)))

    def test_it_needs_papers_dates_and_components(self):
        exam = self.make_exam(status="draft", papers=())
        self.assertError(self.client.post(f"{EXAMS}{exam.pk}/schedule/"), 400, "no_papers")

        ExamSubject.objects.create(organization=self.org, exam=exam, subject=self.physics, level=11)
        r = self.client.post(f"{EXAMS}{exam.pk}/schedule/")
        self.assertError(r, 400, "incomplete")
        self.assertEqual(len(r.data["error"]["details"]["problems"]), 2)     # no date/time, no components

    def test_only_a_draft_can_be_scheduled(self):
        exam = self.make_exam()
        self.assertError(self.client.post(f"{EXAMS}{exam.pk}/schedule/"), 409, "not_draft")

    def test_unschedule_removes_the_calendar_event(self):
        exam = self.make_exam()
        r = self.client.post(f"{EXAMS}{exam.pk}/unschedule/")
        self.assertEqual((r.status_code, r.data["status"]), (200, "draft"))
        self.assertFalse(CalendarEvent.objects.filter(kind="exam").exists())

    def test_unschedule_is_refused_once_marks_exist(self):
        exam = self.make_exam()
        self.fill(self.sheet(exam, self.physics, self.section_a), {self.ram: 70, self.shyam: 50})
        self.assertError(self.client.post(f"{EXAMS}{exam.pk}/unschedule/"), 409, "marks_entered")

    def test_a_published_exam_cannot_be_edited(self):
        exam = self.make_exam()
        exam.status = "published"
        exam.save(update_fields=["status"])
        self.assertError(self.client.patch(f"{EXAMS}{exam.pk}/", {"name": "New name"}), 409, "exam_published")
        paper = self.paper_of(exam, self.physics)
        self.assertError(self.client.patch(f"{PAPERS}{paper.pk}/", {"date": D(5)}), 409, "exam_published")

    def test_a_teacher_cannot_set_up_exams(self):
        self.login(self.hari)
        r = self.client.post(EXAMS, {"campus": self.campus.pk, "academic_year": self.year.pk,
                                     "exam_type": self.terminal.pk, "program": self.program.pk, "name": "X"})
        self.assertEqual(r.status_code, 403)
