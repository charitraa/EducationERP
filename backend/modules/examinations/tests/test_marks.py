"""Entering marks: who may, what's checked, and how a sheet moves on."""
from decimal import Decimal

from core.audit.models import AuditLog
from tests.factories import create_section
from modules.students.services import place_student

from ..models import Mark, MarkCorrection
from .base import API, TODAY, ExamTestCase

SHEETS = f"{API}/mark-sheets/"


class MarkSheetTestCase(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.exam = self.make_exam()                 # physics 3 days ago, math 2 days ago
        self.physics_paper = self.paper_of(self.exam, self.physics)
        self.theory = self.physics_paper.components.get()

    def open_sheet(self, who, paper=None, section=None):
        self.login(who)
        return self.client.post(SHEETS, {"exam_subject": (paper or self.physics_paper).pk,
                                         "section": (section or self.section_a).pk})

    def entry(self, student, marks=None, status="present", component=None):
        return {"enrollment": self.enrollment(student, self.physics_paper.date).pk,
                "component": (component or self.theory).pk, "status": status,
                **({} if marks is None else {"marks": str(marks)})}

    def enter(self, sheet_id, *entries, reason=""):
        return self.client.post(f"{SHEETS}{sheet_id}/marks/", {"entries": list(entries), "reason": reason},
                                format="json")


class OpenSheetTests(MarkSheetTestCase):
    def test_the_papers_teacher_opens_it_and_reopening_returns_the_same_sheet(self):
        first = self.open_sheet(self.hari)
        second = self.open_sheet(self.hari)

        self.assertEqual((first.status_code, second.status_code), (201, 200), first.data)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual((first.data["status"], first.data["section_name"]), ("open", "Grade 11 A"))

    def test_a_teacher_of_another_subject_cannot_open_it(self):
        self.assertError(self.open_sheet(self.sita), 403, "not_your_class")

    def test_the_exam_office_can_open_any_sheet(self):
        self.assertEqual(self.open_sheet(self.office).status_code, 201)

    def test_reopening_an_already_open_sheet_is_still_permission_checked(self):
        self.open_sheet(self.hari)                    # creates it
        self.assertError(self.open_sheet(self.sita), 403, "not_your_class")

    def test_a_paper_that_hasnt_been_sat_has_no_sheet_yet(self):
        future = self.paper(self.exam, self.biology, days_ago=-5, start="14:00", end="16:00")
        self.assertError(self.open_sheet(self.office, paper=future), 400, "future_paper")

    def test_a_class_at_another_level_doesnt_sit_the_paper(self):
        other = create_section(self.campus, self.program, self.year, level=12, name="A")
        self.assertError(self.open_sheet(self.office, section=other), 400, "wrong_section")

    def test_an_exam_being_set_up_has_no_sheets(self):
        draft = self.make_exam("Draft", status="draft")
        paper = self.paper_of(draft, self.physics)
        self.assertError(self.open_sheet(self.office, paper=paper), 409, "not_scheduled")

    def test_someone_from_another_organization_cannot_reach_the_paper(self):
        from tests.factories import create_organization, user_with_system_role
        stranger = user_with_system_role(create_organization(code="other"), "campus-admin", email="x@other.test")
        self.assertEqual(self.open_sheet(stranger).status_code, 400)


class RosterTests(MarkSheetTestCase):
    def roster(self, sheet_id):
        return self.client.get(f"{SHEETS}{sheet_id}/roster/")

    def test_a_compulsory_paper_lists_the_whole_class_and_its_components(self):
        sheet_id = self.open_sheet(self.hari).data["id"]
        r = self.roster(sheet_id)

        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([s["student_name"] for s in r.data["students"]], ["Ram Student", "Shyam Student"])
        self.assertEqual(r.data["components"][0]["full_marks"], 100.0)
        self.assertEqual(r.data["students"][0]["marks"][str(self.theory.pk)], None)

    def test_an_elective_paper_lists_only_the_students_who_chose_it(self):
        paper = self.paper(self.exam, self.computer, days_ago=1, start="14:00", end="16:00")
        self.login(self.sita)
        sheet_id = self.client.post(SHEETS, {"exam_subject": paper.pk, "section": self.section_a.pk}).data["id"]

        names = [s["student_name"] for s in self.roster(sheet_id).data["students"]]
        self.assertEqual(names, ["Ram Student"])                   # Shyam takes biology

    def test_marks_entered_so_far_show_up(self):
        sheet_id = self.open_sheet(self.hari).data["id"]
        self.enter(sheet_id, self.entry(self.ram, 72), self.entry(self.shyam, status="absent"))

        students = {s["student_name"]: s["marks"][str(self.theory.pk)] for s in self.roster(sheet_id).data["students"]}
        self.assertEqual(students["Ram Student"], {"status": "present", "marks": 72.0})
        self.assertEqual(students["Shyam Student"], {"status": "absent", "marks": None})

    def test_another_teacher_cannot_read_the_roster(self):
        sheet_id = self.open_sheet(self.hari).data["id"]
        self.login(self.sita)
        self.assertEqual(self.roster(sheet_id).status_code, 403)

    def test_history_holds_when_a_student_changes_class_after_the_paper(self):
        place_student(student=self.shyam, section=self.section_b, on_date=TODAY)
        sheet_id = self.open_sheet(self.hari).data["id"]

        names = [s["student_name"] for s in self.roster(sheet_id).data["students"]]
        self.assertEqual(names, ["Ram Student", "Shyam Student"])   # Shyam was in A on the paper date
        self.login(self.hari)
        b = self.client.post(SHEETS, {"exam_subject": self.physics_paper.pk, "section": self.section_b.pk})
        b_names = [s["student_name"] for s in self.roster(b.data["id"]).data["students"]]
        self.assertEqual(b_names, ["Binu Student", "Gita Student"])


class EnterMarksTests(MarkSheetTestCase):
    def setUp(self):
        super().setUp()
        self.sheet_id = self.open_sheet(self.hari).data["id"]

    def test_enter_marks(self):
        r = self.enter(self.sheet_id, self.entry(self.ram, "72.5"), self.entry(self.shyam, 40))

        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual({m["student_name"]: Decimal(m["marks"]) for m in r.data},
                         {"Ram Student": Decimal("72.50"), "Shyam Student": Decimal("40.00")})

    def test_editing_while_open_is_free_and_leaves_no_correction(self):
        self.enter(self.sheet_id, self.entry(self.ram, 60))
        self.enter(self.sheet_id, self.entry(self.ram, 65))

        self.assertEqual(Mark.objects.get().marks, Decimal("65.00"))
        self.assertFalse(MarkCorrection.objects.exists())

    def test_marks_must_be_within_the_component(self):
        for bad in ("101", "-1"):
            self.assertError(self.enter(self.sheet_id, self.entry(self.ram, bad)), 400, "marks_out_of_range")

    def test_full_marks_are_fine_and_so_is_zero(self):
        r = self.enter(self.sheet_id, self.entry(self.ram, 100), self.entry(self.shyam, 0))
        self.assertEqual(r.status_code, 200, r.data)

    def test_a_present_student_needs_marks_and_an_absent_one_has_none(self):
        self.assertError(self.enter(self.sheet_id, self.entry(self.ram)), 400, "marks_required")
        self.assertError(self.enter(self.sheet_id, self.entry(self.ram, 50, status="absent")), 400, "marks_not_allowed")

    def test_absent_exempt_and_withheld_are_recorded_without_marks(self):
        r = self.enter(self.sheet_id, self.entry(self.ram, status="absent"), self.entry(self.shyam, status="withheld"))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(set(Mark.objects.values_list("status", flat=True)), {"absent", "withheld"})

    def test_a_student_from_another_class_is_refused(self):
        stranger = {"enrollment": self.enrollment(self.gita).pk, "component": self.theory.pk,
                    "status": "present", "marks": "50"}
        self.assertError(self.enter(self.sheet_id, stranger), 400, "not_expected")

    def test_a_student_who_doesnt_take_the_subject_is_refused(self):
        paper = self.paper(self.exam, self.computer, days_ago=1, start="14:00", end="16:00")
        self.login(self.sita)
        sheet = self.client.post(SHEETS, {"exam_subject": paper.pk, "section": self.section_a.pk}).data["id"]
        component = paper.components.get()
        shyam = {"enrollment": self.enrollment(self.shyam, paper.date).pk, "component": component.pk,
                 "status": "present", "marks": "50"}
        self.assertError(self.enter(sheet, shyam), 400, "not_expected")

    def test_a_component_of_another_paper_is_refused(self):
        other = self.paper_of(self.exam, self.math).components.get()
        self.assertError(self.enter(self.sheet_id, self.entry(self.ram, 50, component=other)), 400, "bad_component")

    def test_listing_a_student_twice_is_refused(self):
        self.assertError(self.enter(self.sheet_id, self.entry(self.ram, 50), self.entry(self.ram, 60)), 400, "duplicate")

    def test_an_empty_request_is_refused(self):
        self.assertEqual(self.enter(self.sheet_id).status_code, 400)

    def test_another_teacher_cannot_enter_marks(self):
        self.login(self.sita)
        self.assertError(self.enter(self.sheet_id, self.entry(self.ram, 50)), 403, "not_your_class")

    def test_nothing_is_saved_when_one_entry_is_bad(self):
        self.enter(self.sheet_id, self.entry(self.ram, 50), self.entry(self.shyam, 500))
        self.assertFalse(Mark.objects.exists())

    def test_two_components_each_have_their_own_limit(self):
        from ..models import ExamComponent
        practical = ExamComponent.objects.create(organization=self.org, exam_subject=self.physics_paper,
                                                 kind="practical", name="Practical", full_marks=25, pass_marks=10)
        self.theory.full_marks, self.theory.pass_marks = 75, 27
        self.theory.save()
        self.assertEqual(self.enter(self.sheet_id, self.entry(self.ram, 70), self.entry(self.ram, 20, component=practical)).status_code, 200)
        self.assertError(self.enter(self.sheet_id, self.entry(self.shyam, 26, component=practical)), 400, "marks_out_of_range")


class SubmitAndVerifyTests(MarkSheetTestCase):
    def setUp(self):
        super().setUp()
        self.sheet_id = self.open_sheet(self.hari).data["id"]

    def submit(self):
        return self.client.post(f"{SHEETS}{self.sheet_id}/submit/")

    def test_submitting_needs_everyone_marked_and_names_who_isnt(self):
        self.enter(self.sheet_id, self.entry(self.ram, 60))
        r = self.submit()

        self.assertError(r, 409, "unmarked")
        self.assertEqual([m["student"] for m in r.data["error"]["details"]["missing"]], ["Shyam Student"])

    def test_submit_then_the_sheet_is_locked_to_the_teacher(self):
        self.enter(self.sheet_id, self.entry(self.ram, 60), self.entry(self.shyam, 50))
        r = self.submit()
        self.assertEqual((r.status_code, r.data["status"]), (200, "submitted"))

        self.assertError(self.submit(), 409, "already_submitted")
        self.assertError(self.enter(self.sheet_id, self.entry(self.ram, 90)), 409, "sheet_locked")

    def test_absent_students_count_as_marked(self):
        self.enter(self.sheet_id, self.entry(self.ram, 60), self.entry(self.shyam, status="absent"))
        self.assertEqual(self.submit().status_code, 200)

    def test_only_the_office_verifies(self):
        self.enter(self.sheet_id, self.entry(self.ram, 60), self.entry(self.shyam, 50))
        self.submit()
        self.assertEqual(self.client.post(f"{SHEETS}{self.sheet_id}/verify/").status_code, 403)   # Hari

        self.login(self.office)
        r = self.client.post(f"{SHEETS}{self.sheet_id}/verify/")
        self.assertEqual((r.status_code, r.data["status"]), (200, "verified"))
        self.assertEqual(self.client.post(f"{SHEETS}{self.sheet_id}/verify/").status_code, 200)   # again: harmless

    def test_open_marks_cannot_be_verified(self):
        self.login(self.office)
        self.assertError(self.client.post(f"{SHEETS}{self.sheet_id}/verify/"), 409, "not_submitted")

    def test_the_office_sends_a_sheet_back_with_a_reason(self):
        self.enter(self.sheet_id, self.entry(self.ram, 60), self.entry(self.shyam, 50))
        self.submit()
        self.login(self.office)

        self.assertEqual(self.client.post(f"{SHEETS}{self.sheet_id}/send-back/", {}).status_code, 400)
        r = self.client.post(f"{SHEETS}{self.sheet_id}/send-back/", {"reason": "Ram's mark looks wrong"})
        self.assertEqual((r.status_code, r.data["status"], r.data["review_note"]),
                         (200, "open", "Ram's mark looks wrong"))

        self.login(self.hari)
        self.assertEqual(self.enter(self.sheet_id, self.entry(self.ram, 62)).status_code, 200)   # free again
        self.assertFalse(MarkCorrection.objects.exists())

    def test_a_sheet_that_is_still_open_cannot_be_sent_back(self):
        self.login(self.office)
        self.assertError(self.client.post(f"{SHEETS}{self.sheet_id}/send-back/", {"reason": "x"}), 409, "already_open")

    def test_status_changes_are_audited(self):
        self.enter(self.sheet_id, self.entry(self.ram, 60), self.entry(self.shyam, 50))
        self.submit()
        self.assertTrue(AuditLog.objects.filter(module="examinations", object_id=str(self.sheet_id)).exists())


class CorrectionTests(MarkSheetTestCase):
    def setUp(self):
        super().setUp()
        self.sheet_id = self.open_sheet(self.hari).data["id"]
        self.enter(self.sheet_id, self.entry(self.ram, 60), self.entry(self.shyam, 50))
        self.client.post(f"{SHEETS}{self.sheet_id}/submit/")
        self.mark = Mark.objects.get(enrollment=self.enrollment(self.ram, self.physics_paper.date))

    def test_the_office_corrects_a_submitted_mark_with_a_reason(self):
        self.login(self.office)
        r = self.enter(self.sheet_id, self.entry(self.ram, 66), reason="Added up wrongly")

        self.assertEqual(r.status_code, 200, r.data)
        correction = MarkCorrection.objects.get()
        self.assertEqual((correction.old_marks, correction.new_marks, correction.reason, correction.corrected_by),
                         (Decimal("60.00"), Decimal("66.00"), "Added up wrongly", self.office))
        self.assertTrue(AuditLog.objects.filter(module="examinations", metadata__correction=True).exists())

    def test_a_reason_is_required(self):
        self.login(self.office)
        self.assertError(self.enter(self.sheet_id, self.entry(self.ram, 66)), 400, "reason_required")

    def test_an_unchanged_mark_makes_no_correction(self):
        self.login(self.office)
        self.enter(self.sheet_id, self.entry(self.ram, 60), reason="Checked")
        self.assertFalse(MarkCorrection.objects.exists())

    def test_correcting_through_the_mark_endpoint(self):
        self.login(self.office)
        r = self.client.patch(f"{API}/marks/{self.mark.pk}/", {"marks": "61", "reason": "Recount"}, format="json")

        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Decimal(r.data["marks"]), Decimal("61.00"))
        self.assertEqual(r.data["corrections"][0]["reason"], "Recount")

    def test_a_teacher_cannot_correct_after_submission(self):
        r = self.client.patch(f"{API}/marks/{self.mark.pk}/", {"marks": "99", "reason": "please"}, format="json")
        self.assertError(r, 409, "sheet_locked")

    def test_a_correction_changes_absent_to_present_and_back(self):
        self.login(self.office)
        self.enter(self.sheet_id, self.entry(self.ram, status="absent"), reason="Was ill, sat a re-test")
        self.enter(self.sheet_id, self.entry(self.ram, 70), reason="Re-test marks")
        self.assertEqual(MarkCorrection.objects.count(), 2)
        self.assertEqual(Mark.objects.get(pk=self.mark.pk).marks, Decimal("70.00"))


class MyPapersTests(MarkSheetTestCase):
    def test_a_teacher_sees_the_papers_they_teach_and_their_sheets(self):
        self.open_sheet(self.hari)
        r = self.client.get(f"{SHEETS}mine/")

        self.assertEqual(r.status_code, 200, r.data)
        rows = {(i["subject_name"], i["section_name"]): i for i in r.data}
        self.assertEqual(set(rows), {("Physics", "Grade 11 A"), ("Physics", "Grade 11 B")})
        self.assertEqual((rows[("Physics", "Grade 11 A")]["status"], rows[("Physics", "Grade 11 B")]["status"]),
                         ("open", None))
        self.assertTrue(all(i["held"] for i in r.data))

    def test_someone_without_a_staff_profile_has_none(self):
        self.login(self.principal)
        self.assertEqual(self.client.get(f"{SHEETS}mine/").status_code, 404)
