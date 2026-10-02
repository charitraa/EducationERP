import json
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core.files.models import StoredFile
from core.files.tests.test_files import PDF
from modules.alumni.models import AlumniProfile
from modules.applications.models import Application, ApplicationType, ApprovalStep
from modules.careers.models import Candidacy, Interview, JobPosting, Vacancy
from modules.hr.models import Contract, Position
from modules.notifications.models import Notification
from modules.staff.models import StaffMember
from tests.base import APITestCaseBase
from tests.factories import (
    create_campus,
    create_organization,
    create_staff_member,
    create_student,
    create_user,
    user_with_system_role,
)

API = "/api/v1"
PUBLIC = f"{API}/public/organizations/kmc/careers"
CANDIDATE = {"first_name": "Binod", "last_name": "Rai", "email": "binod@mail.test", "phone": "9800000009",
             "resume": {"summary": "Physics teacher", "education": [{"institution": "TU", "qualification": "MSc",
                                                                       "year": 2075}],
                        "experience": [{"employer": "Budhanilkantha", "title": "Teacher",
                                        "start_date": "2019-04-01", "end_date": "2023-03-31"}],
                        "skills": ["Physics", "Lab work"]}}


class CareersTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        self.campus = create_campus(self.org, code="main")
        self.branch = create_campus(self.org, code="branch")
        self.office = user_with_system_role(self.org, "campus-admin", email="office@kmc.test", campus=self.campus)
        self.branch_office = user_with_system_role(self.org, "campus-admin", email="branch@kmc.test",
                                                   campus=self.branch)
        self.form = ApplicationType.objects.create(organization=self.org, code="teacher-hiring", name="Teacher hiring",
                                                   kind="job", is_public=True)
        for n, (name, permission) in enumerate([("Screening", "careers.manage"), ("Principal", "careers.hire")], 1):
            ApprovalStep.objects.create(organization=self.org, application_type=self.form, sequence=n, name=name,
                                        permission=permission)
        self.position = Position.objects.create(organization=self.org, code="lecturer", name="Lecturer")
        self.panel_user = create_user(self.org, email="hod@kmc.test", user_type="teacher")
        self.panelist = create_staff_member(self.campus, employee_number="E-1", user=self.panel_user)
        self.other_teacher = create_user(self.org, email="t2@kmc.test", user_type="teacher")
        create_staff_member(self.campus, employee_number="E-2", user=self.other_teacher)

    def as_user(self, user, method, url, body=None, **kwargs):
        self.authenticate(user)
        return getattr(self.client, method)(f"{API}/{url}", body, format=kwargs.pop("format", "json"), **kwargs)

    def make_vacancy(self, **fields):
        body = {"campus": self.campus.pk, "application_type": self.form.pk, "code": "physics-2083",
                "title": "Physics lecturer", "position": self.position.pk, "requirements": ["MSc Physics"],
                **fields}
        response = self.as_user(self.office, "post", "careers/vacancies/", body)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.as_user(self.office, "post", f"careers/vacancies/{response.data['id']}/open/")
                         .data["status"], "open")
        return Vacancy.objects.get(pk=response.data["id"])

    def apply_publicly(self, vacancy, data=None, resume=PDF, name="cv.pdf"):
        self.client.credentials()
        body = {"data": json.dumps(data or CANDIDATE)}
        if resume is not None:
            body["resume"] = SimpleUploadedFile(name, resume, "application/pdf")
        return self.client.post(f"{PUBLIC}/vacancies/{vacancy.pk}/apply/", body, format="multipart")


class VacancyTests(CareersTestCase):
    def test_lifecycle_and_public_listing(self):
        vacancy = self.make_vacancy()
        self.client.credentials()
        listed = self.client.get(f"{PUBLIC}/vacancies/").data
        self.assertEqual([v["title"] for v in listed], ["Physics lecturer"])
        self.assertNotIn("application_type", listed[0])
        self.assertEqual(self.as_user(self.office, "post", f"careers/vacancies/{vacancy.pk}/close/").data["status"],
                         "closed")
        self.client.credentials()
        self.assertEqual(self.client.get(f"{PUBLIC}/vacancies/").data, [])
        self.assertEqual(self.client.get(f"{PUBLIC}/vacancies/{vacancy.pk}/").status_code, 404)

    def test_rules(self):
        general = ApplicationType.objects.create(organization=self.org, code="g", name="G", kind="general")
        body = {"campus": self.campus.pk, "code": "x", "title": "X"}
        self.assertEqual(self.as_user(self.office, "post", "careers/vacancies/",
                                      {**body, "application_type": general.pk}).status_code, 400)
        private = ApplicationType.objects.create(organization=self.org, code="p", name="P", kind="job")
        self.assertEqual(self.as_user(self.office, "post", "careers/vacancies/",
                                      {**body, "application_type": private.pk}).status_code, 400)
        # Another campus's office can't add one here.
        self.assertEqual(self.as_user(self.branch_office, "post", "careers/vacancies/",
                                      {**body, "application_type": self.form.pk}).status_code, 403)
        # A job is never submitted through the generic form.
        response = self.as_user(create_user(self.org, email="a@kmc.test", user_type="alumni"), "post",
                                "applications/", {"application_type": self.form.pk, "campus": self.campus.pk,
                                                  "data": CANDIDATE})
        self.assertEqual(response.status_code, 400)
        self.client.credentials()
        self.assertEqual(self.client.post(f"{API}/public/organizations/kmc/applications/",
                                          {"application_type": self.form.pk, "campus": self.campus.pk,
                                           "contact": {"name": "X", "phone": "1"}, "data": CANDIDATE},
                                          format="json").status_code, 400)


class ApplyTests(CareersTestCase):
    def setUp(self):
        super().setUp()
        self.vacancy = self.make_vacancy()

    def test_public_apply_with_resume(self):
        response = self.apply_publicly(self.vacancy)
        self.assertEqual(response.status_code, 201, response.data)
        candidacy = Candidacy.objects.get(application__number=response.data["number"])
        self.assertEqual((candidacy.full_name, candidacy.email), ("Binod Rai", "binod@mail.test"))
        self.assertEqual((candidacy.resume.owner_type, candidacy.resume.owner_id), ("careers.candidacy", candidacy.pk))
        self.assertEqual(candidacy.application.data["resume"]["experience"][0]["start_date"], "2019-04-01")
        # The applicant checks on it with the engine's own status endpoint.
        status = self.client.post(f"{API}/public/organizations/kmc/applications/status/",
                                  {"number": response.data["number"], "token": response.data["token"]})
        self.assertEqual(status.data["status"], "in_review")

        self.assertEqual(self.apply_publicly(self.vacancy).data["error"]["code"], "duplicate")

    def test_public_refusals(self):
        before = (Application.objects.count(), StoredFile.objects.count())
        self.assertEqual(self.apply_publicly(self.vacancy, resume=None).data["error"]["code"], "resume_required")
        self.assertEqual(self.apply_publicly(self.vacancy, resume=b"<html></html>").status_code, 400)
        self.assertEqual(self.apply_publicly(self.vacancy, data={"first_name": "X"}).status_code, 400)
        self.assertEqual((Application.objects.count(), StoredFile.objects.count()), before)
        other = create_organization(code="other")
        self.client.credentials()
        response = self.client.post(f"{API}/public/organizations/other/careers/vacancies/{self.vacancy.pk}/apply/",
                                    {"data": "{}"}, format="multipart")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(other.pk)

    def test_signed_in_apply_uses_my_upload(self):
        alumna = create_user(self.org, email="alumna@kmc.test", user_type="alumni")
        intruder = create_user(self.org, email="intruder@kmc.test", user_type="alumni")
        self.authenticate(alumna)
        file_id = self.client.post(f"{API}/files/", {"file": SimpleUploadedFile("cv.pdf", PDF),
                                                     "purpose": "resume"}, format="multipart").data["id"]
        # Someone else can't put my file on their application.
        response = self.as_user(intruder, "post", f"careers/vacancies/{self.vacancy.pk}/apply/",
                                {"data": {**CANDIDATE, "email": "i@mail.test"}, "resume_file": file_id})
        self.assertEqual(response.status_code, 400)
        response = self.as_user(alumna, "post", f"careers/vacancies/{self.vacancy.pk}/apply/",
                                {"data": CANDIDATE, "resume_file": file_id})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Candidacy.objects.get(pk=response.data["id"]).application.applicant, alumna)
        # Staff move posts through HR, not by applying.
        self.assertEqual(self.as_user(self.panel_user, "post", f"careers/vacancies/{self.vacancy.pk}/apply/",
                                      {"data": {**CANDIDATE, "email": "x@mail.test"}}).data["error"]["code"],
                         "already_staff")


class HiringTests(CareersTestCase):
    def setUp(self):
        super().setUp()
        self.vacancy = self.make_vacancy()
        response = self.apply_publicly(self.vacancy)
        self.number, self.token = response.data["number"], response.data["token"]
        self.candidacy = Candidacy.objects.get(application__number=self.number)
        self.application = self.candidacy.application

    def decide(self, verb, **body):
        return self.as_user(self.office, "post", f"applications/{self.application.pk}/{verb}/", body)

    def test_from_screening_to_hired(self):
        # Screening, and an interview with the HOD on the panel.
        self.assertEqual(self.as_user(self.office, "post", f"careers/candidacies/{self.candidacy.pk}/screen/",
                                      {"score": 82, "note": "Strong"}).data["screening_score"], 82)
        when = (timezone.now() + timedelta(days=2)).isoformat()
        response = self.as_user(self.office, "post", "careers/interviews/",
                                {"candidacy": self.candidacy.pk, "scheduled_at": when, "panel": [self.panelist.pk],
                                 "location": "Room 4"})
        self.assertEqual(response.status_code, 201, response.data)
        interview = response.data["id"]
        self.assertTrue(Notification.objects.filter(recipient=self.panel_user,
                                                    event_type="careers.interview_panel").exists())
        self.assertEqual([i["id"] for i in self.as_user(self.panel_user, "get", "careers/interviews/mine/")
                          .data["results"]], [interview])
        self.assertEqual(self.as_user(self.panel_user, "post", f"careers/interviews/{interview}/outcome/",
                                      {"status": "completed", "score": "8.5"}).data["error"]["code"], "not_started")
        Interview.objects.filter(pk=interview).update(scheduled_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.as_user(self.other_teacher, "post", f"careers/interviews/{interview}/outcome/",
                                      {"status": "completed"}).status_code, 404)
        response = self.as_user(self.panel_user, "post", f"careers/interviews/{interview}/outcome/",
                                {"status": "completed", "score": "8.5", "recommendation": "hire",
                                 "feedback": "Clear explanations"})
        self.assertEqual(response.data["status"], "completed", response.data)

        # An offer only at the last step; hiring only with it accepted.
        offer = {"candidacy": self.candidacy.pk, "start_date": (timezone.localdate() + timedelta(days=14)).isoformat(),
                 "salary_note": "Grade 5", "expires_on": (timezone.localdate() + timedelta(days=7)).isoformat()}
        self.assertEqual(self.as_user(self.office, "post", "careers/offers/", offer).data["error"]["code"],
                         "not_last_step")
        self.assertEqual(self.decide("approve").data["step"], 2)
        response = self.as_user(self.office, "post", "careers/offers/", offer)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["contract_kind"], "probation")
        self.assertEqual(self.decide("approve", decision={"employee_number": "E-77"}).data["error"]["code"],
                         "no_accepted_offer")

        self.client.credentials()
        self.assertEqual(self.client.post(f"{PUBLIC}/offer/", {"number": self.number, "token": "nope"}).status_code,
                         404)
        seen = self.client.post(f"{PUBLIC}/offer/", {"number": self.number, "token": self.token})
        self.assertEqual((seen.data["salary_note"], seen.data["status"]), ("Grade 5", "made"))
        self.assertNotIn("made_by", seen.data)
        response = self.client.post(f"{PUBLIC}/offer/respond/", {"number": self.number, "token": self.token,
                                                                 "accept": True})
        self.assertEqual(response.data["status"], "accepted", response.data)

        self.assertEqual(self.decide("approve").status_code, 400)  # the employee number is needed
        self.assertEqual(self.decide("approve", decision={"employee_number": "E-1"}).data["error"]["code"],
                         "duplicate_number")
        response = self.decide("approve", decision={"employee_number": "E-77"})
        self.assertEqual(response.data["status"], "approved", response.data)

        staff = StaffMember.objects.get(employee_number="E-77")
        self.assertEqual((staff.full_name, staff.campus, staff.designation, staff.email, str(staff.joined_on)),
                         ("Binod Rai", self.campus, "Physics lecturer", "binod@mail.test", offer["start_date"]))
        contract = Contract.objects.get(staff=staff)
        self.assertEqual((contract.kind, contract.position, contract.reference), ("probation", self.position,
                                                                                  self.number))
        self.vacancy.refresh_from_db()
        self.assertEqual((self.vacancy.status, self.vacancy.hired_count), ("filled", 1))
        self.candidacy.refresh_from_db()
        self.assertEqual(self.candidacy.hired_staff, staff)

    def test_resume_access(self):
        url = f"files/{self.candidacy.resume_id}/download/"
        self.assertEqual(self.as_user(self.office, "get", url).status_code, 200)
        self.assertEqual(self.as_user(self.branch_office, "get", url).status_code, 404)
        self.assertEqual(self.as_user(self.panel_user, "get", url).status_code, 404)
        when = (timezone.now() + timedelta(days=2)).isoformat()
        self.as_user(self.office, "post", "careers/interviews/", {"candidacy": self.candidacy.pk,
                                                                  "scheduled_at": when, "panel": [self.panelist.pk]})
        self.assertEqual(self.as_user(self.panel_user, "get", url).status_code, 200)
        self.assertEqual(self.as_user(self.other_teacher, "get", url).status_code, 404)

    def test_offer_declined_or_withdrawn(self):
        self.decide("approve")
        start = (timezone.localdate() + timedelta(days=14)).isoformat()
        pk = self.as_user(self.office, "post", "careers/offers/", {"candidacy": self.candidacy.pk,
                                                                   "start_date": start}).data["id"]
        self.assertEqual(self.as_user(self.office, "post", "careers/offers/",
                                      {"candidacy": self.candidacy.pk, "start_date": start}).data["error"]["code"],
                         "live_offer")
        self.client.credentials()
        self.client.post(f"{PUBLIC}/offer/respond/", {"number": self.number, "token": self.token, "accept": False})
        self.assertTrue(Notification.objects.filter(event_type="careers.offer_declined").exists())
        # A new offer can follow a declined one; withdrawing tells the candidate.
        pk = self.as_user(self.office, "post", "careers/offers/", {"candidacy": self.candidacy.pk,
                                                                   "start_date": start}).data["id"]
        self.assertEqual(self.as_user(self.office, "post", f"careers/offers/{pk}/withdraw/", {"reason": "Budget"})
                         .data["status"], "withdrawn")
        self.assertEqual(self.as_user(self.branch_office, "get", f"careers/offers/{pk}/").status_code, 404)

    def test_signed_in_candidate_answers_and_alumni_login_becomes_staff(self):
        alumna = create_user(self.org, email="alumna@kmc.test", user_type="alumni")
        AlumniProfile.objects.create(organization=self.org, campus=self.campus, user=alumna, first_name="A",
                                     last_name="B")
        Vacancy.objects.filter(pk=self.vacancy.pk).update(resume_required=False, openings=2)
        pk = self.as_user(alumna, "post", f"careers/vacancies/{self.vacancy.pk}/apply/",
                          {"data": {**CANDIDATE, "email": "alumna@mail.test"}}).data["id"]
        application = Candidacy.objects.get(pk=pk).application
        self.as_user(self.office, "post", f"applications/{application.pk}/approve/")
        start = (timezone.localdate() + timedelta(days=3)).isoformat()
        offer = self.as_user(self.office, "post", "careers/offers/", {"candidacy": pk, "start_date": start}).data
        self.assertEqual([o["id"] for o in self.as_user(alumna, "get", "careers/offers/mine/").data["results"]],
                         [offer["id"]])
        self.assertEqual(self.as_user(self.other_teacher, "post", f"careers/offers/{offer['id']}/respond/",
                                      {"accept": True}).status_code, 404)
        self.assertEqual(self.as_user(alumna, "post", f"careers/offers/{offer['id']}/respond/", {"accept": True})
                         .data["status"], "accepted")
        response = self.as_user(self.office, "post", f"applications/{application.pk}/approve/",
                                {"decision": {"employee_number": "E-88"}})
        self.assertEqual(response.data["status"], "approved", response.data)
        alumna.refresh_from_db()
        self.assertEqual((StaffMember.objects.get(employee_number="E-88").user, alumna.user_type),
                         (alumna, "teacher"))


class JobBoardTests(CareersTestCase):
    def setUp(self):
        super().setUp()
        self.alumna = create_user(self.org, email="alumna@kmc.test", user_type="alumni")
        AlumniProfile.objects.create(organization=self.org, campus=self.campus, user=self.alumna, first_name="A",
                                     last_name="B")
        self.student_user = create_user(self.org, email="s@kmc.test", user_type="student")
        create_student(self.campus, student_number="S-1", user=self.student_user)
        self.posting = {"title": "Junior developer", "company": "Leapfrog", "description": "Python",
                        "apply_url": "https://example.com/apply", "audience": "both"}

    def test_alumni_post_office_approves_students_read(self):
        response = self.as_user(self.alumna, "post", "careers/postings/", self.posting)
        self.assertEqual((response.status_code, response.data["status"]), (201, "pending"), response.data)
        pk = response.data["id"]
        self.assertTrue(Notification.objects.filter(recipient=self.office,
                                                    event_type="careers.posting_submitted").exists())
        self.assertEqual(self.as_user(self.student_user, "get", "careers/postings/").data["results"], [])
        self.assertEqual(self.as_user(self.student_user, "post", f"careers/postings/{pk}/review/",
                                      {"approve": True}).status_code, 403)
        self.assertEqual(self.as_user(self.office, "post", f"careers/postings/{pk}/review/", {"approve": False})
                         .status_code, 400)  # a reason is needed to turn it down
        self.assertEqual(self.as_user(self.office, "post", f"careers/postings/{pk}/review/", {"approve": True})
                         .data["status"], "approved")
        self.assertEqual([p["id"] for p in self.as_user(self.student_user, "get", "careers/postings/")
                          .data["results"]], [pk])
        # Approved: the poster can't edit it any more, but can take it down.
        self.assertEqual(self.as_user(self.alumna, "patch", f"careers/postings/{pk}/", {"title": "X"}).status_code,
                         403)
        self.assertEqual(self.as_user(self.alumna, "post", f"careers/postings/{pk}/close/").data["status"], "closed")
        self.assertEqual(self.as_user(self.student_user, "get", "careers/postings/").data["results"], [])

    def test_audiences_and_who_posts(self):
        self.as_user(self.office, "post", "careers/postings/", {**self.posting, "audience": "alumni"})
        self.assertEqual(JobPosting.objects.get().status, "approved")  # the office posts straight away
        self.assertEqual(self.as_user(self.student_user, "get", "careers/postings/").data["results"], [])
        self.assertEqual(len(self.as_user(self.alumna, "get", "careers/postings/").data["results"]), 1)
        self.assertEqual(self.as_user(self.student_user, "post", "careers/postings/", self.posting).status_code, 403)
        self.assertEqual(self.as_user(self.alumna, "post", "careers/postings/",
                                      {**self.posting, "apply_url": ""}).status_code, 400)


class PublicTenantTests(CareersTestCase):
    def test_another_organizations_url_reaches_nothing(self):
        vacancy = self.make_vacancy()
        response = self.apply_publicly(vacancy)
        candidacy = Candidacy.objects.get(application__number=response.data["number"])
        self.as_user(self.office, "post", f"applications/{candidacy.application_id}/approve/")
        self.as_user(self.office, "post", "careers/offers/", {
            "candidacy": candidacy.pk, "start_date": (timezone.localdate() + timedelta(days=9)).isoformat()})
        create_organization(code="other")
        self.client.credentials()
        lookup = {"number": response.data["number"], "token": response.data["token"]}
        self.assertEqual(self.client.post(f"{PUBLIC}/offer/", lookup).status_code, 200)
        other = f"{API}/public/organizations/other/careers"
        self.assertEqual(self.client.get(f"{other}/vacancies/").data, [])
        self.assertEqual(self.client.get(f"{other}/vacancies/{vacancy.pk}/").status_code, 404)
        for path in ("offer/", "offer/respond/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(f"{other}/{path}", {"number": response.data["number"],
                                                                      "token": response.data["token"],
                                                                      "accept": True}).status_code, 404)
        # A private (signed-in only) vacancy isn't on the public page at all.
        Vacancy.objects.filter(pk=vacancy.pk).update(is_public=False)
        self.assertEqual(self.client.get(f"{PUBLIC}/vacancies/{vacancy.pk}/").status_code, 404)
        self.assertEqual(self.apply_publicly(vacancy).status_code, 404)
