"""Who can see and do what: roles, campuses, organizations."""
from tests.factories import create_campus, create_organization, user_with_system_role

from ..models import Exam, Result
from .base import API, ExamTestCase

STAFF_ENDPOINTS = ["exam-types", "exams", "exam-subjects", "exam-rooms", "seat-allocations", "invigilations",
                   "admit-cards", "mark-sheets", "marks", "results", "term-results", "grades/scales"]


class AnonymousTests(ExamTestCase):
    def test_every_endpoint_needs_a_login(self):
        for name in STAFF_ENDPOINTS + ["exams/me", "results/me", "report-cards/me", "transcripts/me", "admit-cards/me"]:
            self.assertEqual(self.client.get(f"{API}/{name}/").status_code, 401, name)
        self.assertEqual(self.client.get(f"{API}/report-cards/").status_code, 401)


class RoleTests(ExamTestCase):
    def setUp(self):
        super().setUp()
        self.exam = self.make_exam()
        self.finish_marking(self.exam)

    def test_a_teacher_can_read_scales_and_their_papers_but_not_the_exam_office(self):
        self.login(self.hari)
        self.assertEqual(self.client.get(f"{API}/grades/scales/").status_code, 200)
        self.assertEqual(self.client.get(f"{API}/mark-sheets/mine/").status_code, 200)
        for name in ("exams", "results", "exam-subjects", "admit-cards", "term-results", "exam-types"):
            self.assertEqual(self.client.get(f"{API}/{name}/").status_code, 403, name)
        self.assertEqual(self.client.post(f"{API}/exams/{self.exam.pk}/publish/").status_code, 403)
        self.assertEqual(self.client.post(f"{API}/exams/{self.exam.pk}/schedule/").status_code, 403)

    def test_the_exam_office_can_do_everything_at_its_campus(self):
        self.login(self.office)
        for name in STAFF_ENDPOINTS:
            self.assertEqual(self.client.get(f"{API}/{name}/").status_code, 200, name)

    def test_a_student_has_no_access_to_staff_endpoints(self):
        from tests.factories import create_user
        user = create_user(self.org, email="ram@kmc.test")
        self.ram.user = user
        self.ram.save(update_fields=["user"])
        self.login(user)
        for name in STAFF_ENDPOINTS:
            self.assertEqual(self.client.get(f"{API}/{name}/").status_code, 403, name)
        self.assertEqual(self.client.get(f"{API}/report-cards/", {"exam": self.exam.pk, "student": 1}).status_code, 403)


class CampusScopeTests(ExamTestCase):
    """A campus admin only reaches their own campus's exams."""

    def setUp(self):
        super().setUp()
        self.exam = self.make_exam()
        self.finish_marking(self.exam)
        from ..results import compute_exam_results
        compute_exam_results(self.exam)
        self.elsewhere = user_with_system_role(self.org, "campus-admin", email="other@kmc.test", campus=self.other_campus)

    def test_lists_are_empty_and_details_are_missing(self):
        self.login(self.elsewhere)
        for name in ("exams", "exam-subjects", "mark-sheets", "marks", "results"):
            self.assertEqual(self.client.get(f"{API}/{name}/").data["count"], 0, name)
        self.assertEqual(self.client.get(f"{API}/exams/{self.exam.pk}/").status_code, 404)
        result = Result.objects.first()
        self.assertEqual(self.client.get(f"{API}/results/{result.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"{API}/results/{result.pk}/report-card/").status_code, 404)

    def test_actions_on_another_campuss_exam_are_missing(self):
        self.login(self.elsewhere)
        for action in ("schedule", "unschedule", "publish", "compute", "seat-plan", "generate-admit-cards"):
            self.assertEqual(self.client.post(f"{API}/exams/{self.exam.pk}/{action}/", {}).status_code, 404, action)

    def test_report_cards_and_transcripts_are_campus_limited_too(self):
        self.login(self.elsewhere)
        self.assertEqual(self.client.get(f"{API}/report-cards/", {"exam": self.exam.pk, "section": self.section_a.pk}).data, [])
        self.assertEqual(self.client.get(f"{API}/transcripts/{self.ram.pk}/").status_code, 404)

    def test_cannot_create_an_exam_for_another_campus(self):
        self.login(self.elsewhere)
        r = self.client.post(f"{API}/exams/", {"campus": self.campus.pk, "academic_year": self.year.pk,
                                               "exam_type": self.terminal.pk, "program": self.program.pk, "name": "X"})
        self.assertEqual(r.status_code, 403)

    def test_an_organization_wide_admin_sees_every_campus(self):
        self.login(self.principal)
        self.assertEqual(self.client.get(f"{API}/exams/").data["count"], 1)


class TenantTests(ExamTestCase):
    """Another organization never sees, or can name, this one's records."""

    def setUp(self):
        super().setUp()
        self.exam = self.make_exam()
        self.finish_marking(self.exam)
        self.rival_org = create_organization(code="rival")
        create_campus(self.rival_org, code="main")
        self.rival = user_with_system_role(self.rival_org, "org-admin", email="rival@rival.test")

    def test_nothing_is_visible(self):
        self.login(self.rival)
        for name in STAFF_ENDPOINTS:
            r = self.client.get(f"{API}/{name}/")
            self.assertEqual((r.status_code, r.data["count"]), (200, 0), name)
        self.assertEqual(self.client.get(f"{API}/exams/{self.exam.pk}/").status_code, 404)

    def test_ids_from_another_organization_cannot_be_used(self):
        self.login(self.rival)
        from tests.factories import create_academic_year, create_program
        year = create_academic_year(self.rival_org)
        program = create_program(self.rival_org, first_level=11, last_level=12)
        campus = self.rival_org.campuses.first()
        from ..models import ExamType
        kind = ExamType.objects.create(organization=self.rival_org, code="t", name="T")
        body = {"campus": campus.pk, "academic_year": year.pk, "exam_type": kind.pk, "program": program.pk}
        # Their own ids work once they have a scale...
        self.assertEqual(self.client.post(f"{API}/exams/", {**body, "name": "Mine"}).status_code, 400)   # no scale yet
        # ...ours never do.
        for field, value in (("campus", self.campus.pk), ("academic_year", self.year.pk),
                             ("exam_type", self.terminal.pk), ("program", self.program.pk),
                             ("grade_scale", self.scale.pk)):
            r = self.client.post(f"{API}/exams/", {**body, field: value, "name": "Steal"})
            self.assertEqual(r.status_code, 400, field)
        self.assertEqual(Exam.objects.filter(organization=self.rival_org).count(), 0)

    def test_a_rival_cannot_touch_our_sheets_or_results(self):
        self.login(self.rival)
        sheet = self.exam.subjects.first().sheets.first()
        for url in (f"{API}/mark-sheets/{sheet.pk}/roster/", f"{API}/mark-sheets/{sheet.pk}/verify/"):
            self.assertIn(self.client.generic("GET" if "roster" in url else "POST", url).status_code, (403, 404), url)
        r = self.client.post(f"{API}/mark-sheets/", {"exam_subject": self.exam.subjects.first().pk,
                                                     "section": self.section_a.pk})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get(f"{API}/transcripts/{self.ram.pk}/").status_code, 404)
