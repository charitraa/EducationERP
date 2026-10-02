import io
import zipfile

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from core.files import access
from core.files.models import StoredFile
from core.files.services import attach
from tests.base import APITestCaseBase
from tests.factories import create_campus, create_organization, create_user

API = "/api/v1"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def docx(macro=False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
        if macro:
            archive.writestr("word/vbaProject.bin", b"\x00")
    return buffer.getvalue()


class FileTestCase(APITestCaseBase):
    def setUp(self):
        self.org = create_organization(code="kmc")
        create_campus(self.org)
        self.sita = create_user(self.org, email="sita@kmc.test", user_type="alumni")
        self.hari = create_user(self.org, email="hari@kmc.test", user_type="alumni")

    def upload(self, user, name, content, purpose="resume", content_type="application/octet-stream"):
        self.authenticate(user)
        return self.client.post(f"{API}/files/", {"file": SimpleUploadedFile(name, content, content_type),
                                                  "purpose": purpose}, format="multipart")


class UploadTests(FileTestCase):
    def test_accepted_types(self):
        for name, content in (("cv.pdf", PDF), ("CV.DOCX", docx())):
            with self.subTest(name=name):
                response = self.upload(self.sita, name, content)
                self.assertEqual(response.status_code, 201, response.data)
                self.assertEqual(response.data["size"], len(content))
                self.assertFalse(response.data["is_attached"])

    def test_type_comes_from_the_bytes_not_the_name_or_header(self):
        cases = [
            ("cv.pdf", b"<html><script>alert(1)</script></html>", "application/pdf"),  # HTML dressed as PDF
            ("cv.docx", docx(macro=True), "application/msword"),  # a macro document
            ("cv.docx", PDF, "application/pdf"),  # extension disagrees with the bytes
            ("cv.png", PNG, "image/png"),  # a real image, but résumés are PDF or Word
            ("cv.svg", b"<svg onload=alert(1)/>", "image/svg+xml"),
            ("cv.pdf", b"", "application/pdf"),
        ]
        for name, content, header in cases:
            with self.subTest(name=name, header=header):
                response = self.upload(self.sita, name, content, content_type=header)
                self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(StoredFile.objects.exists())

    @override_settings(FILE_UPLOAD_MAX_BYTES=100)
    def test_size_limit(self):
        response = self.upload(self.sita, "cv.pdf", PDF + b"x" * 200)
        self.assertEqual(response.data["error"]["code"], "file_too_large")

    def test_unknown_purpose(self):
        self.assertEqual(self.upload(self.sita, "cv.pdf", PDF, purpose="avatar").status_code, 400)

    def test_name_is_cleaned_and_path_is_ours(self):
        response = self.upload(self.sita, "../../etc/<evil>cv.pdf", PDF)
        stored = StoredFile.objects.get(pk=response.data["id"])
        self.assertEqual(stored.name, "evilcv.pdf")
        self.assertNotIn("evil", stored.file.name)
        self.assertTrue(stored.file.name.startswith(f"{self.org.pk}/"))
        # Kept in the private storage, nowhere under the public media folder.
        self.assertFalse(stored.file.path.startswith(str(settings.MEDIA_ROOT)))

    def test_anonymous_cannot_upload(self):
        response = self.client.post(f"{API}/files/", {"file": SimpleUploadedFile("cv.pdf", PDF),
                                                      "purpose": "resume"}, format="multipart")
        self.assertEqual(response.status_code, 401)


class AccessTests(FileTestCase):
    def setUp(self):
        super().setUp()
        self.pk = self.upload(self.sita, "cv.pdf", PDF).data["id"]

    def test_only_the_uploader_sees_an_unattached_file(self):
        self.authenticate(self.sita)
        response = self.client.get(f"{API}/files/{self.pk}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), PDF)
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual([f["id"] for f in self.client.get(f"{API}/files/").data["results"]], [self.pk])

        self.authenticate(self.hari)
        self.assertEqual(self.client.get(f"{API}/files/{self.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"{API}/files/{self.pk}/download/").status_code, 404)
        self.assertEqual(self.client.get(f"{API}/files/").data["results"], [])

    def test_another_organization_never_sees_it(self):
        other = create_organization(code="other")
        outsider = create_user(other, email="x@other.test")
        self.authenticate(outsider)
        self.assertEqual(self.client.get(f"{API}/files/{self.pk}/download/").status_code, 404)

    def test_attached_files_follow_their_owners_rule(self):
        stored = StoredFile.objects.get(pk=self.pk)
        # Only the uploader can attach it, and only once.
        with self.assertRaises(Exception):
            attach(stored, owner_type="test.thing", owner_id=1, purpose="resume", by=self.hari)
        attach(stored, owner_type="test.thing", owner_id=1, purpose="resume", by=self.sita)
        with self.assertRaises(Exception):
            attach(stored, owner_type="test.thing", owner_id=2, purpose="resume", by=self.sita)

        # No rule registered for the owner type: nobody reads it, uploader included.
        self.authenticate(self.sita)
        self.assertEqual(self.client.get(f"{API}/files/{self.pk}/download/").status_code, 404)
        access.register("test.thing", lambda user, owner_id: user.pk == self.hari.pk)
        self.addCleanup(access._CHECKS.pop, "test.thing", None)
        self.authenticate(self.hari)
        self.assertEqual(self.client.get(f"{API}/files/{self.pk}/download/").status_code, 200)
        # And it stays with its record.
        self.authenticate(self.sita)
        self.assertEqual(self.client.delete(f"{API}/files/{self.pk}/").status_code, 404)

    def test_uploader_removes_an_unused_file(self):
        stored = StoredFile.objects.get(pk=self.pk)
        self.authenticate(self.hari)
        self.assertEqual(self.client.delete(f"{API}/files/{self.pk}/").status_code, 404)
        self.authenticate(self.sita)
        self.assertEqual(self.client.delete(f"{API}/files/{self.pk}/").status_code, 204)
        self.assertFalse(StoredFile.all_objects.filter(pk=self.pk).exists())
        self.assertFalse(stored.file.storage.exists(stored.file.name))
