"""The OpenAPI schema documents what each endpoint actually reads.

A viewset whose ``serializer_class`` is read-only, with ``create()`` reading
another serializer, gets no request body in the schema unless it says so —
and a client generated from the schema then can't call it.
"""
from django.test import SimpleTestCase
from drf_spectacular.generators import SchemaGenerator

# (path, the fields the endpoint reads)
TAKES_A_BODY = [
    ("/api/v1/library/issues/", {"copy", "member"}),
    ("/api/v1/library/reservations/", {"book", "member"}),
    ("/api/v1/student-awards/", {"student", "award", "note"}),
    ("/api/v1/communication/appointments/", {"slot", "student", "reason"}),
    ("/api/v1/events/{id}/register/", {"note"}),
]


class RequestBodyTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    def test_creates_with_a_separate_input_serializer_document_it(self):
        components = self.schema["components"]["schemas"]
        for path, fields in TAKES_A_BODY:
            with self.subTest(path=path):
                body = self.schema["paths"][path]["post"].get("requestBody")
                self.assertIsNotNone(body, f"{path} documents no request body")
                ref = body["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
                self.assertEqual(set(components[ref]["properties"]), fields)
